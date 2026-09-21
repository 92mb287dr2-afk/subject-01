"""Local launcher; viewing a demo never implicitly creates the official life."""
import sys
from pathlib import Path

from . import birth, observer
from .continuity import ContinuityStore, code_hash


def main():
    identity = birth.read_identity()
    if identity and identity["status"] == "born":
        sys.argv = [sys.argv[0], "--subject"]
        observer.main()
        return
    print("Subject-01\nEnter — открыть тестовую среду.\n1 — подготовить первое рождение единственного существа.")
    if input("Выбор: ").strip() != "1":
        if Path("data-candidate/continuity.sqlite3").exists():
            store = ContinuityStore("data-candidate")
            try:
                state = store.load(lazy=True)
                prior = state["continuity"]["code_hash"] if state else code_hash()
            finally:
                store.close()
            if prior != code_hash():
                print("Код тестовой среды изменился. Обновление сохранит её историю; это не официальное существо.")
                print("Для подтверждения напечатайте: ОБНОВИТЬ КАНДИДАТ")
                birth.upgrade_candidate("data-candidate", prior, input("> ").strip())
        sys.argv = [sys.argv[0], "--candidate"]
        observer.main()
        return
    report = birth.release_manifest()
    print("Рождение начнёт новую официальную историю. Тестовая история не переносится.")
    print("Следующий запуск продолжит ту же жизнь. Выключение останавливает время мира.")
    print("Полный журнал требует места: ориентир около 6 ГБ за сутки работы при 20 Гц.")
    print("При нехватке места мир остановится без удаления памяти. Нужны свободный диск и архивирование.")
    print("Это исследовательская модель: наличие сознания не доказано. Отчёт: docs/RELEASE_STATUS.md")
    print("Проверки: " + report.get("evidence_url", "см. отчёт релиза"))
    plan = birth.prepare(identity["data_dir"] if identity else "data-subject")
    print("Для отдельного подтверждения напечатайте: " + plan["confirmation"])
    phrase = input("> ").strip()
    if phrase != plan["confirmation"]:
        print("Рождение не выполнено. Подготовленное намерение сохранено для следующего запуска.")
        return
    birth.confirm(phrase)
    sys.argv = [sys.argv[0], "--subject"]
    observer.main()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print("Запуск остановлен: " + str(exc))
        raise SystemExit(1)
