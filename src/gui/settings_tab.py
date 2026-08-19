from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gui.prefs import (
    WATCH_ALL,
    WATCH_CRATE,
    normalize_auto_drink_watch,
    normalize_quick_buff_key,
)
from gui.theme import ACCENTS

_ACCENT_LABELS = ("Blue", "Dark-blue", "Green")
_ACCENT_KEYS = ("blue", "dark-blue", "green")
_WINDOW_MODES = ("normal", "frameless", "fullscreen")
_WINDOW_KEYS = ("window_normal", "window_frameless", "window_fullscreen")


class SettingsTab(QWidget):
    language_changed = Signal(str)
    accent_changed = Signal(str)
    window_mode_changed = Signal(str)
    switches_changed = Signal()

    def __init__(self, i18n, prefs, log_fn, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._prefs = prefs
        self._log = log_fn
        self._filling = False

        self._language_label = QLabel(self._i18n.t("language"))
        self._language = QComboBox(self)
        self._language.currentIndexChanged.connect(self._on_language)

        self._theme_label = QLabel(self._i18n.t("color_theme"))
        self._theme = QComboBox(self)
        for label, key in zip(_ACCENT_LABELS, _ACCENT_KEYS):
            self._theme.addItem(label, key)
        self._theme.currentIndexChanged.connect(self._on_accent)

        self._window_label = QLabel(self._i18n.t("window_mode"))
        self._window = QComboBox(self)
        self._window.currentIndexChanged.connect(self._on_window_mode)

        self._auto_drink = QCheckBox(self._i18n.t("auto_drink"), self)
        self._auto_drink.toggled.connect(self._on_auto_drink)

        self._drink_panel = QWidget(self)
        drink_layout = QVBoxLayout(self._drink_panel)
        drink_layout.setContentsMargins(18, 4, 0, 4)
        drink_layout.setSpacing(4)
        self._watch_all = QRadioButton(self._i18n.t("auto_drink_watch_all"), self)
        self._watch_crate = QRadioButton(self._i18n.t("auto_drink_watch_crate"), self)
        self._watch_hint = QLabel(self._i18n.t("auto_drink_watch_hint"), self)
        self._watch_hint.setWordWrap(True)
        self._watch_hint.setObjectName("hintLabel")
        self._watch_group = QButtonGroup(self)
        self._watch_group.addButton(self._watch_all)
        self._watch_group.addButton(self._watch_crate)
        self._watch_all.toggled.connect(self._on_watch)
        self._watch_crate.toggled.connect(self._on_watch)
        drink_layout.addWidget(self._watch_all)
        drink_layout.addWidget(self._watch_crate)
        drink_layout.addWidget(self._watch_hint)

        self._probe = QCheckBox(self._i18n.t("projectile_probe"), self)
        self._probe.toggled.connect(self._on_switch)

        self._buff_label = QLabel(self._i18n.t("quick_buff_key"))
        self._buff = QLineEdit(self)
        self._buff.setMaxLength(4)
        self._buff.setFixedWidth(60)
        self._buff.editingFinished.connect(self._on_buff_finished)
        self._buff.returnPressed.connect(self._on_buff_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._language_label)
        layout.addWidget(self._language, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(8)
        layout.addWidget(self._theme_label)
        layout.addWidget(self._theme, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(8)
        layout.addWidget(self._window_label)
        layout.addWidget(self._window, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(12)
        layout.addWidget(self._auto_drink)
        layout.addWidget(self._drink_panel)
        layout.addSpacing(8)
        layout.addWidget(self._probe)
        layout.addSpacing(12)
        layout.addWidget(self._buff_label)
        layout.addWidget(self._buff, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        self._rebuild_language_items()
        self._rebuild_window_items()
        self.load_from_prefs()

    def _rebuild_language_items(self):
        self._filling = True
        current = self._language.currentData()
        self._language.clear()
        self._language.addItem(self._i18n.t("language_en"), "en")
        self._language.addItem(self._i18n.t("language_ru"), "ru")
        if current in ("en", "ru"):
            self._set_combo_data(self._language, current)
        self._filling = False

    def _rebuild_window_items(self):
        self._filling = True
        current = self._window.currentData()
        self._window.clear()
        for key, mode in zip(_WINDOW_KEYS, _WINDOW_MODES):
            self._window.addItem(self._i18n.t(key), mode)
        if current in _WINDOW_MODES:
            self._set_combo_data(self._window, current)
        self._filling = False

    def _set_combo_data(self, combo, data):
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def load_from_prefs(self):
        self._filling = True
        lang = self._prefs.get("language", "ru")
        self._set_combo_data(self._language, lang if lang in ("en", "ru") else "en")
        theme = str(self._prefs.get("Color Theme", "blue")).lower()
        if theme not in ACCENTS:
            theme = "blue"
        self._set_combo_data(self._theme, theme)
        mode = self._prefs.get("window_mode", "normal")
        if mode not in _WINDOW_MODES:
            mode = "normal"
        self._set_combo_data(self._window, mode)
        self._auto_drink.setChecked(bool(self._prefs.get("auto drink")))
        watch = normalize_auto_drink_watch(self._prefs.get("auto_drink_watch"))
        self._watch_all.setChecked(watch != WATCH_CRATE)
        self._watch_crate.setChecked(watch == WATCH_CRATE)
        self._drink_panel.setVisible(self._auto_drink.isChecked())
        self._probe.setChecked(bool(self._prefs.get("projectile_probe")))
        self._buff.setText(self._prefs.get("quick_buff_key", "b"))
        self._filling = False

    def retranslate(self):
        self._language_label.setText(self._i18n.t("language"))
        self._theme_label.setText(self._i18n.t("color_theme"))
        self._window_label.setText(self._i18n.t("window_mode"))
        self._auto_drink.setText(self._i18n.t("auto_drink"))
        self._watch_all.setText(self._i18n.t("auto_drink_watch_all"))
        self._watch_crate.setText(self._i18n.t("auto_drink_watch_crate"))
        self._watch_hint.setText(self._i18n.t("auto_drink_watch_hint"))
        self._probe.setText(self._i18n.t("projectile_probe"))
        self._buff_label.setText(self._i18n.t("quick_buff_key"))
        current_lang = self._language.currentData()
        current_mode = self._window.currentData()
        self._rebuild_language_items()
        self._rebuild_window_items()
        self._filling = True
        if current_lang:
            self._set_combo_data(self._language, current_lang)
        if current_mode:
            self._set_combo_data(self._window, current_mode)
        self._filling = False

    def auto_drink_on(self):
        return self._auto_drink.isChecked()

    def auto_drink_watch(self):
        watch = WATCH_CRATE if self._watch_crate.isChecked() else WATCH_ALL
        self._prefs["auto_drink_watch"] = watch
        return watch

    def probe_on(self):
        return self._probe.isChecked()

    def window_mode(self):
        data = self._window.currentData()
        return data if data in _WINDOW_MODES else "normal"

    def quick_buff_key(self):
        typed = self._buff.text()
        normalized = normalize_quick_buff_key(typed)
        if normalized != typed.strip().lower():
            self._log(self._i18n.t("invalid_buff_key", key=normalized) + "\n\n")
        self._buff.setText(normalized)
        self._prefs["quick_buff_key"] = normalized
        return normalized

    def _on_language(self, _index):
        if self._filling:
            return
        lang = self._language.currentData()
        if lang not in ("en", "ru"):
            return
        if lang == self._prefs.get("language"):
            return
        self._prefs["language"] = lang
        self.language_changed.emit(lang)

    def _on_accent(self, _index):
        if self._filling:
            return
        key = self._theme.currentData()
        if key not in ACCENTS:
            return
        self._prefs["Color Theme"] = key
        label = self._theme.currentText()
        self._log(self._i18n.t("theme_applied", theme=label) + "\n\n")
        self.accent_changed.emit(key)

    def _on_window_mode(self, _index):
        if self._filling:
            return
        mode = self._window.currentData()
        if mode not in _WINDOW_MODES:
            return
        self._prefs["window_mode"] = mode
        self.window_mode_changed.emit(mode)

    def _on_switch(self, _checked=False):
        if self._filling:
            return
        self.switches_changed.emit()

    def _on_auto_drink(self, _checked=False):
        if self._filling:
            return
        self._drink_panel.setVisible(self._auto_drink.isChecked())
        self.switches_changed.emit()

    def _on_watch(self, _checked=False):
        if self._filling or not _checked:
            return
        self.auto_drink_watch()
        self.switches_changed.emit()

    def _on_buff_finished(self):
        if self._filling:
            return
        self.quick_buff_key()
        self.switches_changed.emit()
