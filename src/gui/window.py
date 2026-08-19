import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from catches_data import catch_display_name, ids_for_en_keys
from gui.auto_drink import AutoDrink
from gui.bridge import BotBridge
from gui.catch_tab import CatchTab
from gui.paths import probe_log_path, resource_path
from gui.prefs import (
    format_window_geometry,
    normalize_cast_aim,
    parse_tk_geometry,
    save_preferences,
)
from gui.settings_tab import SettingsTab
from gui.stats_tab import StatsTab
from gui.theme import accent_hex, apply_theme
from gui.widgets import AnimatedButton, FishingLog, TitleBar
from memory_bot import MemoryBot

_MIN_W, _MIN_H = 720, 480
_DEFAULT_W, _DEFAULT_H = 1100, 680
_MAX_WIDGET = 16777215


class MainWindow(QMainWindow):
    def __init__(self, prefs, i18n, catches, statistics, paths):
        super().__init__()
        self.prefs = prefs
        self.i18n = i18n
        self.catches = catches
        self.statistics = statistics
        self.paths = paths
        self.memory_bot = None
        self.auto_drink = AutoDrink()
        self._window_mode = "normal"
        self._windowed_geometry = None

        self.bridge = BotBridge(self)
        self.bridge.catch_id.connect(self.on_catch, Qt.ConnectionType.QueuedConnection)
        self.bridge.status.connect(self.on_status, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self.on_error, Qt.ConnectionType.QueuedConnection)
        self.bridge.aim_saved.connect(self.on_aim_saved, Qt.ConnectionType.QueuedConnection)

        icon_path = resource_path("icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.catch_tab.set_selected_en_keys(self.prefs.get("Catch List", []))
        self.log_view.append_log(f"{self.i18n.t('fishing_log')}\n\n")
        self.apply_accent(self.prefs.get("Color Theme", "blue"))
        self.restore_geometry_from_prefs()
        self.apply_window_mode(self.prefs.get("window_mode", "normal"))
        self.retranslate()

    def _build_ui(self):
        root = QWidget(self)
        root.setObjectName("centralRoot")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self.i18n, root)
        self.title_bar.close_requested.connect(self.close)
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.hide()
        outer.addWidget(self.title_bar)

        body = QWidget(root)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 16, 16)
        body_layout.setSpacing(0)

        left = QWidget(body)
        left.setObjectName("leftPanel")
        left.setMinimumWidth(260)
        left.setMaximumWidth(360)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 12, 16)
        left_layout.setSpacing(6)

        self.title_label = QLabel(self.i18n.t("app_title"), left)
        self.title_label.setObjectName("titleLabel")

        self.start_button = AnimatedButton(self.i18n.t("start"), left)
        self.stop_button = AnimatedButton(self.i18n.t("stop"), left)
        self.aim_button = AnimatedButton(self.i18n.t("aim_button"), left)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_clicked)
        self.stop_button.clicked.connect(self.stop_clicked)
        self.aim_button.clicked.connect(self.aim_clicked)

        self.status_label = QLabel(self.i18n.t("status_idle"), left)
        self.status_label.setWordWrap(True)

        self.log_view = FishingLog(self.i18n, left)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.copy_log_button = AnimatedButton(self.i18n.t("log_copy_all"), left)
        self.copy_log_button.clicked.connect(self.log_view.copy_all)

        left_layout.addWidget(self.title_label)
        left_layout.addWidget(self.start_button)
        left_layout.addWidget(self.stop_button)
        left_layout.addWidget(self.aim_button)
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(self.log_view, 1)
        left_layout.addWidget(self.copy_log_button)

        self.main_tabs = QTabWidget(body)
        self.catch_tab = CatchTab(
            self.i18n,
            self.prefs,
            self.catches["names"],
            self.catches["by_en"],
            self.statistics,
            self._save_prefs,
            None,
        )
        self.catch_tab.select_all_done.connect(self._on_select_all_done)
        self.stats_tab = StatsTab(
            self.i18n,
            self.prefs,
            self.catches["by_en"],
            self.statistics,
            self.main_tabs,
        )
        self.settings_tab = SettingsTab(
            self.i18n, self.prefs, self.log_message, self.main_tabs
        )
        self.settings_tab.language_changed.connect(self._on_language_changed)
        self.settings_tab.accent_changed.connect(self._on_accent_changed)
        self.settings_tab.window_mode_changed.connect(self.apply_window_mode)
        self.settings_tab.switches_changed.connect(self.save_switch_preferences)

        self.main_tabs.addTab(self.catch_tab, self.i18n.t("tab_catch"))
        self.main_tabs.addTab(self.stats_tab, self.i18n.t("statistics"))
        self.main_tabs.addTab(self.settings_tab, self.i18n.t("tab_options"))

        body_layout.addWidget(left, 1)
        body_layout.addWidget(self.main_tabs, 2)
        outer.addWidget(body, 1)

    def _save_prefs(self):
        save_preferences(self.paths["preferences_json"], self.prefs)

    def _save_statistics(self):
        with open(self.paths["statistics_json"], "w", encoding="utf-8") as f:
            json.dump(self.statistics, f, indent=4, ensure_ascii=False)

    def current_lang(self):
        return self.prefs.get("language", "ru")

    def catch_name_for_id(self, item_id):
        entry = self.catches["by_id"].get(int(item_id))
        if entry:
            return catch_display_name(entry, self.current_lang())
        return self.i18n.t("unknown_item", id=item_id)

    def _bot_running(self):
        return (
            self.memory_bot is not None
            and self.memory_bot.thread is not None
            and self.memory_bot.thread.is_alive()
        )

    def log_message(self, text):
        self.log_view.append_log(text)

    def apply_accent(self, key):
        hex_color = accent_hex(key)
        from PySide6.QtWidgets import QApplication

        apply_theme(QApplication.instance(), key)
        for btn in (
            self.start_button,
            self.stop_button,
            self.aim_button,
            self.copy_log_button,
        ):
            btn.set_accent(hex_color)
        self.catch_tab.set_accent(hex_color)

    def apply_window_mode(self, mode):
        if mode not in ("normal", "frameless", "fullscreen"):
            mode = "normal"
        if self.isVisible() and self._window_mode != "fullscreen":
            self.store_geometry_to_prefs()
        self._window_mode = mode
        self.prefs["window_mode"] = mode
        self._save_prefs()
        self.setWindowTitle(self.i18n.t("app_title"))

        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setMaximumSize(_MAX_WIDGET, _MAX_WIDGET)

        if mode == "fullscreen":
            self.title_bar.hide()
            self.setWindowFlags(Qt.WindowType.Window)
            self.showFullScreen()
            return

        if mode == "frameless":
            self.setWindowFlags(
                Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            )
            self.title_bar.show()
            self.show()
            self.restore_geometry_from_prefs()
            self.setFixedSize(self.size())
            return

        self.title_bar.hide()
        self.setWindowFlags(Qt.WindowType.Window)
        self.show()
        self.restore_geometry_from_prefs()

    def restore_geometry_from_prefs(self):
        parsed = parse_tk_geometry(self.prefs.get("window_geometry") or "1100x680")
        if not parsed:
            self.resize(_DEFAULT_W, _DEFAULT_H)
            return
        width, height, x, y = parsed
        width = max(width, _MIN_W)
        height = max(height, _MIN_H)
        self.resize(width, height)
        if x is not None and y is not None:
            self.move(x, y)
        self._windowed_geometry = (width, height, x, y)

    def store_geometry_to_prefs(self):
        if self._window_mode == "fullscreen":
            return
        geo = self.geometry()
        self.prefs["window_geometry"] = format_window_geometry(
            geo.width(), geo.height(), geo.x(), geo.y()
        )
        self._windowed_geometry = (geo.width(), geo.height(), geo.x(), geo.y())

    def retranslate(self):
        self.setWindowTitle(self.i18n.t("app_title"))
        self.title_label.setText(self.i18n.t("app_title"))
        self.start_button.setText(self.i18n.t("start"))
        self.stop_button.setText(self.i18n.t("stop"))
        self.aim_button.setText(self.i18n.t("aim_button"))
        self.copy_log_button.setText(self.i18n.t("log_copy_all"))
        if not self._bot_running():
            self.status_label.setText(self.i18n.t("status_idle"))
        self.title_bar.retranslate()
        self.log_view.retranslate()
        self.main_tabs.setTabText(0, self.i18n.t("tab_catch"))
        self.main_tabs.setTabText(1, self.i18n.t("statistics"))
        self.main_tabs.setTabText(2, self.i18n.t("tab_options"))
        self.catch_tab.retranslate()
        self.stats_tab.retranslate()
        self.settings_tab.retranslate()

    def _on_language_changed(self, lang):
        self.prefs["language"] = lang
        self.i18n.load(lang)
        self._save_prefs()
        self.prefs["Catch List"] = self.catch_tab.get_selected_en_keys()
        self.retranslate()
        self.stats_tab.update_counts()

    def _on_accent_changed(self, key):
        self.apply_accent(key)
        self._save_prefs()

    def _on_select_all_done(self, count):
        self.log_message(self.i18n.t("selected_all", count=count) + "\n\n")

    def start_clicked(self):
        key = self.settings_tab.quick_buff_key()
        self._save_prefs()
        en_keys = self.catch_tab.get_selected_en_keys()
        if not en_keys:
            self.log_message(self.i18n.t("no_catches") + "\n")
            self.log_message(self.i18n.t("please_select") + "\n")
            return

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.catch_tab.set_controls_enabled(False)

        item_ids = ids_for_en_keys(en_keys, self.catches["by_en"])
        self.memory_bot = MemoryBot(
            on_catch=self.bridge.on_catch,
            on_status=self.bridge.on_status,
            on_error=self.bridge.on_error,
            whitelist_ids=set(item_ids),
            poll_interval=0.025,
            aim_client=self.prefs.get("cast_aim"),
            on_aim=self.bridge.on_aim,
            probe_enabled=bool(self.prefs.get("projectile_probe")),
            probe_log_path=str(probe_log_path()),
            on_input_busy=self.auto_drink.set_suspended,
        )
        self.log_message(self.i18n.t("mem_looking") + "\n")
        self.log_message(self.i18n.t("hook_started") + "\n")
        if self.prefs.get("projectile_probe"):
            self.log_message(
                self.i18n.t("projectile_probe_file", path=str(probe_log_path())) + "\n"
            )
        self.log_message("\n")
        self.save_switch_preferences()
        self.memory_bot.start()

    def stop_clicked(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.catch_tab.set_controls_enabled(True)
        self.auto_drink.stop()
        if self.memory_bot is not None:
            self.memory_bot.stop()
            self.memory_bot = None
        self.log_message(self.i18n.t("hook_stopped") + "\n\n")
        self.status_label.setText(self.i18n.t("status_idle"))
        self.save_switch_preferences()

    def aim_clicked(self):
        self.prefs["cast_aim"] = None
        self._save_prefs()
        if self._bot_running():
            self.memory_bot.request_aim()
        self.status_label.setText(self.i18n.t("aim_button"))
        self.log_message(self.i18n.t("aim_prompt") + "\n")

    def on_aim_saved(self, x, y):
        self.prefs["cast_aim"] = normalize_cast_aim([int(x), int(y)])
        self._save_prefs()

    def on_status(self, msg: str):
        if msg == "scanning":
            self.log_message(self.i18n.t("mem_scanning") + "\n")
        elif msg == "scanning_heap":
            self.log_message(self.i18n.t("mem_scanning_heap") + "\n")
        elif msg == "scanning_heap_full":
            self.log_message(self.i18n.t("mem_scanning_heap_full") + "\n")
        elif msg.startswith("progress:"):
            pct = msg.split(":", 1)[1]
            self.log_message(self.i18n.t("mem_scanning_pct", pct=pct) + "\n")
        elif msg in ("hooked", "hooked_cached"):
            self.status_label.setText(self.i18n.t("mem_hooked"))
            self.log_message(self.i18n.t("mem_hooked") + "\n")
            self._maybe_start_auto_drink()
        elif msg == "aim_prompt":
            self.status_label.setText(self.i18n.t("aim_button"))
            self.log_message(self.i18n.t("aim_prompt") + "\n")
        elif msg.startswith("aim_set:"):
            self.status_label.setText(self.i18n.t("aim_set"))
            self.log_message(self.i18n.t("aim_set") + "\n")
        elif msg == "scan_aborted":
            return
        elif msg.startswith(("rolled:", "phase:", "click:")):
            return
        elif msg.startswith("bite:"):
            parts = msg.split(":")
            if len(parts) >= 3 and parts[2] == "skip":
                try:
                    item_id = int(parts[1])
                except ValueError:
                    return
                name = self.catch_name_for_id(item_id)
                self.log_message(
                    self.i18n.t("mem_skip", id=item_id, name=name) + "\n"
                )
        elif msg == "reel_failed":
            self.log_message(self.i18n.t("mem_reel_failed") + "\n")
        elif msg == "recast_failed":
            self.log_message(self.i18n.t("mem_recast_failed") + "\n")
        elif msg == "focus_failed":
            self.log_message(self.i18n.t("mem_focus_failed") + "\n")
        elif msg == "auto_drink_buffs_missing":
            self.log_message(self.i18n.t("auto_drink_buffs_missing") + "\n")
            self.auto_drink.stop()
        elif msg.startswith("safe_stop:"):
            reason = msg.split(":", 1)[1]
            reason_key = f"mem_safe_stop_reason_{reason}"
            reason_text = self.i18n.t(reason_key)
            if reason_text == reason_key:
                reason_text = reason
            self.log_message(
                self.i18n.t("mem_safe_stop", reason=reason_text) + "\n\n"
            )
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.catch_tab.set_controls_enabled(True)
            self.status_label.setText(self.i18n.t("status_idle"))
            self.auto_drink.stop()
            self.memory_bot = None

    def on_error(self, err: str):
        if err == "terraria_not_running":
            self.log_message(self.i18n.t("mem_not_running") + "\n\n")
        elif err == "terraria_is_64bit":
            self.log_message(self.i18n.t("mem_need_x86") + "\n\n")
        elif err == "signature_not_found":
            self.log_message(self.i18n.t("mem_sig_missing") + "\n\n")
        elif err == "context_null":
            self.log_message(self.i18n.t("mem_context_null") + "\n\n")
        elif err == "player_not_found":
            self.log_message(self.i18n.t("mem_player_missing") + "\n\n")
        else:
            self.log_message(self.i18n.t("mem_error", error=err) + "\n\n")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.catch_tab.set_controls_enabled(True)
        self.status_label.setText(self.i18n.t("status_idle"))
        self.auto_drink.stop()

    def on_catch(self, item_id):
        entry = self.catches["by_id"].get(int(item_id))
        if entry:
            en_key = entry["en"]
            display = catch_display_name(entry, self.current_lang())
        else:
            en_key = None
            display = self.i18n.t("unknown_item", id=item_id)
        self.log_message(self.i18n.t("caught", name=display) + "\n")
        if not en_key:
            return
        for key in self.statistics.keys():
            if en_key in self.statistics[key]:
                self.statistics[key][en_key] += 1
                self.stats_tab.update_counts()
                self._save_statistics()
                break

    def save_switch_preferences(self):
        self.prefs["auto drink"] = self.settings_tab.auto_drink_on()
        self.prefs["auto_drink_watch"] = self.settings_tab.auto_drink_watch()
        probe_on = self.settings_tab.probe_on()
        was_probe = bool(self.prefs.get("projectile_probe"))
        self.prefs["projectile_probe"] = probe_on
        self._save_prefs()
        if self.memory_bot is not None:
            self.memory_bot.set_probe_enabled(probe_on)
        if probe_on and not was_probe:
            self.log_message(
                self.i18n.t("projectile_probe_file", path=str(probe_log_path())) + "\n"
            )
        self._maybe_start_auto_drink()

    def _player_source(self):
        bot = self.memory_bot
        if bot is None:
            return None, 0
        return bot.process_handle, bot.local_player_ptr()

    def _maybe_start_auto_drink(self):
        if not self.prefs.get("auto drink"):
            self.auto_drink.stop()
            return
        if self.memory_bot is None or not self.memory_bot.local_player_ptr():
            return
        watch = self.settings_tab.auto_drink_watch()
        if self.auto_drink.running():
            self.auto_drink.set_watch(watch)
            return
        self.auto_drink.start(
            self.settings_tab.quick_buff_key(),
            self._player_source,
            watch=watch,
            on_status=self.bridge.on_status,
        )

    def closeEvent(self, event):
        self.store_geometry_to_prefs()
        self.prefs["Catch List"] = self.catch_tab.get_selected_en_keys()
        self.prefs["window_mode"] = self._window_mode
        if self.memory_bot is not None:
            self.memory_bot.stop()
            self.memory_bot = None
        self.auto_drink.stop()
        self._save_prefs()
        event.accept()
