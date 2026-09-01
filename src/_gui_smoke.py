"""Headless GUI smoke test for the new journal events.

Builds MainWindow offscreen, feeds it every new status (caught_recovery,
line_snapped, bite_escaped, reel_retrying) through the bridge, and checks
the fishing log text plus statistics.json accounting.
"""
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

qt_app = QApplication(sys.argv)

from catches_data import load_catches  # noqa: E402
from gui.prefs import DEFAULT_PREFERENCES  # noqa: E402
from gui.window import MainWindow  # noqa: E402
from i18n import I18n  # noqa: E402


def resource_path(rel):
    return SRC / rel


prefs = dict(DEFAULT_PREFERENCES)
prefs["language"] = "ru"
i18n = I18n(resource_path)
i18n.load("ru")

_, names, by_en, _d2e, by_id = load_catches(resource_path)

with tempfile.TemporaryDirectory() as tmp:
    stats_path = Path(tmp) / "statistics.json"
    prefs_path = Path(tmp) / "preferences.json"
    stats = {"Fish": {"Bass": 0}}
    window = MainWindow(
        prefs=prefs,
        i18n=i18n,
        catches={"names": names, "by_en": by_en, "by_id": by_id},
        statistics=stats,
        paths={
            "app_dir": Path(tmp),
            "preferences_json": prefs_path,
            "statistics_json": stats_path,
            "fishing_log": Path(tmp) / "fishing_log.txt",
            "events_log": Path(tmp) / "fishing_events.jsonl",
        },
    )

    BASS_ID = 2290
    window.on_catch(BASS_ID)
    window.on_status(f"caught_recovery:{BASS_ID}")
    window.on_status(f"line_snapped:{BASS_ID}")
    window.on_status(f"bite_escaped:{BASS_ID}")
    window.on_status(f"reel_retrying:{BASS_ID}")
    qt_app.processEvents()

    log_text = window.log_view.toPlainText()
    checks = {
        "caught_recovery": ("Поймано: Окунь" in log_text),
        "line_snapped": ("Леска оборвалась — упущено: Окунь" in log_text),
        "bite_escaped": ("Клёв ушёл: Окунь" in log_text),
        "reel_retrying": ("повторяю подсечку (Окунь)" in log_text),
        "no_reel_failed_dup": (
            "Подсечка не прошла. Проверь точку заброса" not in log_text
        ),
        "stats_incremented": stats["Fish"]["Bass"] == 2,
        "stats_saved": stats_path.exists(),
    }
    if stats_path.exists():
        saved = json.loads(stats_path.read_text(encoding="utf-8"))
        checks["stats_file_content"] = saved.get("Fish", {}).get("Bass") == 2

    events_path = Path(tmp) / "fishing_events.jsonl"
    lines = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [rec["event"] for rec in lines]
    checks["events_file"] = True
    checks["event_catch"] = events.count("catch") == 1
    checks["event_catch_recovered"] = events.count("catch_recovered") == 1
    checks["event_line_snapped"] = events.count("line_snapped") == 1
    checks["event_bite_escaped"] = events.count("bite_escaped") == 1
    checks["events_have_names"] = all(
        rec.get("item") == "Окунь"
        for rec in lines
        if rec["event"]
        in ("catch", "catch_recovered", "line_snapped", "bite_escaped")
    )
    checks["events_have_time"] = all(rec.get("time") for rec in lines)

    window.on_status("calibrating")
    window.on_status("calibrated")
    window.on_status("calibrate_timeout")
    window.on_status("anim_dead")
    qt_app.processEvents()
    log_text = window.log_view.toPlainText()
    checks["status_calibrating"] = "Калибровка поплавка" in log_text
    checks["status_calibrated"] = "Поплавок распознан" in log_text
    checks["status_calibrate_timeout"] = (
        "работаю без проверки поплавка" in log_text
    )
    checks["status_anim_dead"] = "работаю по сигналу поклёвки" in log_text
    checks["anim_dead_not_scary"] = (
        "Клики не поднимают анимацию предмета" not in log_text
    )
    window.on_status("initial_cast")
    qt_app.processEvents()
    log_text = window.log_view.toPlainText()
    checks["status_initial_cast"] = "Забрасываю удочку" in log_text

    log_file = Path(tmp) / "fishing_log.txt"
    checks["log_file_exists"] = log_file.exists()
    if log_file.exists():
        file_text = log_file.read_text(encoding="utf-8")
        checks["log_file_has_events"] = "Леска оборвалась" in file_text
        first_line = file_text.split("\n", 1)[0].strip()
        header = i18n.t("fishing_log")
        checks["log_file_no_header"] = first_line != header
        window.log_view.clear_log()
        qt_app.processEvents()
        after = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        checks["clear_button_truncates_file"] = (
            window.clear_log_button.isEnabled() and len(after) == 0
        )
        checks["clear_button_cleared_view"] = (
            window.log_view.toPlainText() == ""
        )

    failed = [k for k, ok in checks.items() if not ok]
    for k, ok in checks.items():
        print(("PASS" if ok else "FAIL"), k)
    if failed:
        print("\nLog excerpt:\n" + log_text)
        print("\nEvents:\n" + "\n".join(map(str, lines)))
        raise SystemExit(1)
    print("\nGUI smoke passed.")
    print("Example event records:")
    for rec in lines:
        print(json.dumps(rec, ensure_ascii=False))

raise SystemExit(0)
