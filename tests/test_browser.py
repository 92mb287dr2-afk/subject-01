"""Opt-in real Chromium integration tests. CI sets RUN_BROWSER=1."""
import os
import time

import pytest

from test_observer import live_server

pytestmark = pytest.mark.skipif(os.environ.get("RUN_BROWSER") != "1",
                                reason="enable RUN_BROWSER with Playwright installed")


def test_browser_world_graph_and_mobile(live_server):
    from playwright.sync_api import sync_playwright

    runtime, server, url = live_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_function("document.querySelector('#connection').textContent === 'МИР АКТИВЕН'")
        page.wait_for_function("Number(document.querySelector('#created').textContent) > 0",
                               timeout=15000)
        assert page.evaluate("state.brain.nodes.length") == 32
        assert page.evaluate("state.brain.edges.length") > 56
        before = page.evaluate("state.objects.length")
        page.locator("#add").click()
        page.locator("#world").click(position={"x": 130, "y": 140})
        page.wait_for_function(f"state.objects.length === {before + 1}")
        page.locator("#save").click()
        page.wait_for_function("document.querySelector('#notice').textContent.includes('сохранено')")
        page.locator("#expand").click()
        assert "expanded" in page.locator("#brainPanel").get_attribute("class")
        page.locator("#zoomIn").click()
        assert page.evaluate("camera.zoom") > 1
        # Use real mouse for pointer capture and node selection.
        xy = page.evaluate("""() => {
            const r=document.querySelector('#brain').getBoundingClientRect();
            const p=point('h0',r.width,r.height);return {x:r.left+p.x,y:r.top+p.y};
        }""")
        page.mouse.click(xy["x"], xy["y"])
        assert "h0" in page.locator("#nodeInfo").inner_text()
        page.keyboard.press("Escape")
        page.select_option("#filter", "signal")
        page.wait_for_selector(".event.signal")
        os.makedirs("test-artifacts", exist_ok=True)
        page.screenshot(path="test-artifacts/observer-desktop.png", full_page=True)
        tick = runtime.core.tick_index
        page.close()
        time.sleep(.15)
        assert runtime.core.tick_index > tick
        mobile = browser.new_page(viewport={"width": 390, "height": 844},
                                  device_scale_factor=2, is_mobile=True, has_touch=True)
        mobile.on("pageerror", lambda error: errors.append(str(error)))
        mobile.goto(url)
        mobile.wait_for_function("document.querySelector('#connection').textContent === 'МИР АКТИВЕН'")
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path="test-artifacts/observer-mobile.png", full_page=True)
        assert not errors
        browser.close()
