import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from subject01.candidate import CandidateRuntime
from subject01.observer import ObserverServer


@pytest.fixture
def candidate_server(tmp_path):
    runtime = CandidateRuntime.open(tmp_path)
    server = ObserverServer(runtime, 0)
    runtime.start()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
    thread.start()
    try:
        yield runtime, server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        runtime.stop()


def test_candidate_http_durable_commands_inspection_and_protection(candidate_server):
    runtime, server, url = candidate_server
    headers = {"Content-Type": "application/json", "X-Observer-Token": server.token}
    def post(path, data):
        return json.load(urlopen(Request(url + path, data=json.dumps(data).encode(), headers=headers)))
    command = dict(request_id="http-id", kind="spawn_object", payload=dict(x=10, y=15))
    first = post("/api/command", command)
    assert post("/api/command", command) == first
    state = json.load(urlopen(url + "/api/state"))
    assert state["continuity"]["origin_id"] == runtime.metadata["origin_id"]
    assert state["life"]["memories"]
    assert state["life"]["memory_page"]["shown"] <= 100
    memories = json.load(urlopen(url + "/api/memories?after=0"))
    assert memories["records"][0]["memory_id"] == 1
    with urlopen(url + "/api/memory-export") as response:
        exported = [json.loads(line) for line in response.read().splitlines()]
    assert exported[0]["kind"] == "memory_export"
    assert exported[1]["memory_id"] == 1
    journal = json.load(urlopen(url + "/api/journal"))
    assert journal["cursor"] > 0
    with pytest.raises(HTTPError) as rejected:
        post("/api/command", dict(kind="delete_memory", payload=dict(memory_id=1)))
    assert rejected.value.code == 400
    with urlopen(url + "/api/neural-log") as response:
        batches = [json.loads(line) for line in response.read().splitlines()]
    assert batches and batches[0]["events"][0]["kind"] == "candidate_initialized"
    post("/api/save", {})


def test_candidate_ui_layers_memory_mobile_and_reconnect(candidate_server):
    import os
    if os.environ.get("RUN_BROWSER") != "1":
        pytest.skip("Opt-in Chromium verification")
    from playwright.sync_api import sync_playwright, expect
    runtime, server, url = candidate_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#connection")).to_have_text("МИР АКТИВЕН")
        expect(page.locator("#developmentPanel")).to_be_visible()
        page.locator("#pauseWorld").click()
        expect(page.locator("#connection")).to_have_text("МИР НА ПАУЗЕ")
        tick = page.locator("#tick").inner_text()
        page.wait_for_timeout(200)
        assert page.locator("#tick").inner_text() == tick
        page.locator("#pauseWorld").click()
        expect(page.locator("#connection")).to_have_text("МИР АКТИВЕН")
        for layer in ("controller", "researcher", "brain"):
            page.select_option("#graphLayer", layer)
            expect(page.locator("#nodeCount")).to_contain_text("32" if layer == "brain" else "37")
        page.select_option("#filter", "development")
        expect(page.locator("#events .event").first).to_be_visible()
        page.get_by_text("Полное состояние и защищённая память", exact=True).click()
        page.get_by_text("Исключительное вмешательство владельца", exact=True).click()
        page.locator("#previewOverride").click()
        expect(page.locator("#overrideConsequence")).to_contain_text("будет удалено")
        page.locator("#overrideConfirmation").fill("wrong confirmation")
        page.locator("#confirmOverride").click()
        expect(page.locator("#notice")).to_contain_text("Confirmation does not match")
        page.get_by_text("Исключительное вмешательство владельца", exact=True).click()
        page.locator("#inspectState").click()
        expect(page.locator("#fullState")).to_contain_text("integrity_hash")
        page.locator("#firstMemories").click()
        expect(page.locator("#memoryPage")).to_contain_text("integrity_hash")
        expect(page.locator("#lifeStatus")).to_have_text("PRE-BIRTH")
        page.get_by_text("Полное состояние и защищённая память", exact=True).click()
        os.makedirs("test-artifacts", exist_ok=True)
        page.screenshot(path="test-artifacts/candidate-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path="test-artifacts/candidate-mobile.png", full_page=True)
        page.route("**/api/frames?**", lambda route: route.abort())
        expect(page.locator("#connection")).to_have_text("НЕТ LIVE-СВЯЗИ")
        page.unroute("**/api/frames?**")
        expect(page.locator("#connection")).to_have_text("МИР АКТИВЕН")
        assert not errors
        browser.close()
