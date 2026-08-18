import json
import os
import shutil
import sys
from multiprocessing import freeze_support

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from catches_data import load_catches
from gui.paths import get_app_dir, resource_path
from gui.prefs import load_preferences
from gui.window import MainWindow
from i18n import I18n


def main():
    freeze_support()

    app_dir = get_app_dir()
    if getattr(sys, "frozen", False):
        os.chdir(app_dir)

    statistics_json = app_dir.joinpath("statistics.json")
    preferences_json = app_dir.joinpath("preferences.json")

    if getattr(sys, "frozen", False):
        bundled_prefs = resource_path("preferences.json")
        bundled_stats = resource_path("statistics.json")
        if not preferences_json.exists() and bundled_prefs.exists():
            shutil.copy(bundled_prefs, preferences_json)
        if not statistics_json.exists() and bundled_stats.exists():
            shutil.copy(bundled_stats, statistics_json)

    with open(statistics_json, encoding="utf-8") as f:
        statistics_data = json.load(f)

    preferences = load_preferences(preferences_json)
    i18n = I18n(resource_path)
    i18n.load(preferences.get("language", "ru"))

    _, catch_names, catches_by_en, _display_to_en, catches_by_id = load_catches(
        resource_path
    )

    qt_app = QApplication(sys.argv)
    qt_app.setFont(QFont("Segoe UI", 10))
    icon_path = resource_path("icon.ico")
    if icon_path.exists():
        qt_app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(
        prefs=preferences,
        i18n=i18n,
        catches={
            "names": catch_names,
            "by_en": catches_by_en,
            "by_id": catches_by_id,
        },
        statistics=statistics_data,
        paths={
            "app_dir": app_dir,
            "preferences_json": preferences_json,
            "statistics_json": statistics_json,
        },
    )
    raise SystemExit(qt_app.exec())


if __name__ == "__main__":
    main()
