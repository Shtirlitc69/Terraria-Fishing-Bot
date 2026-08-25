from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from catches_data import catch_display_name
from gui.widgets import SecondaryButton


class CatchTab(QWidget):
    selection_changed = Signal()
    select_all_done = Signal(int)

    def __init__(self, i18n, prefs, catch_names, catches_by_en, statistics, save_prefs, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._prefs = prefs
        self._catch_names = list(catch_names)
        self._catches_by_en = catches_by_en
        self._statistics = statistics
        self._save_prefs = save_prefs
        self._suspend = False
        self._checks = {}
        self._visible = {}
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._apply_filter)

        self._title = QLabel(self._i18n.t("catch_list"))
        self._title.setObjectName("sectionTitle")

        self._search = QLineEdit(self)
        self._search.setPlaceholderText(self._i18n.t("catch_search"))
        self._search.textChanged.connect(self._on_search_text)

        preset_row = QWidget(self)
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(8)
        self._preset_fish = SecondaryButton(self._i18n.t("preset_fish"), preset_row)
        self._preset_quest = SecondaryButton(self._i18n.t("preset_quest"), preset_row)
        self._preset_crates = SecondaryButton(self._i18n.t("preset_crates"), preset_row)
        self._preset_fish.clicked.connect(lambda: self._select_preset("Fish"))
        self._preset_quest.clicked.connect(lambda: self._select_preset("Quest Fish"))
        self._preset_crates.clicked.connect(lambda: self._select_preset("Crates"))
        preset_layout.addWidget(self._preset_fish)
        preset_layout.addWidget(self._preset_quest)
        preset_layout.addWidget(self._preset_crates)

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch(1)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._list_host)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        actions = QWidget(self)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self._clear = SecondaryButton(self._i18n.t("clear"), actions)
        self._select_all = SecondaryButton(self._i18n.t("select_all"), actions)
        self._clear.clicked.connect(self.clear_clicked)
        self._select_all.clicked.connect(self._on_select_all)
        actions_layout.addWidget(self._clear)
        actions_layout.addWidget(self._select_all)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(self._title)
        layout.addWidget(self._search)
        layout.addWidget(preset_row)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(actions)

        self._build_catch_checkboxes()

    def retranslate(self):
        self._title.setText(self._i18n.t("catch_list"))
        self._search.setPlaceholderText(self._i18n.t("catch_search"))
        self._preset_fish.setText(self._i18n.t("preset_fish"))
        self._preset_quest.setText(self._i18n.t("preset_quest"))
        self._preset_crates.setText(self._i18n.t("preset_crates"))
        self._clear.setText(self._i18n.t("clear"))
        self._select_all.setText(self._i18n.t("select_all"))
        self.refresh_labels()

    def display_name_for_en(self, en_key):
        entry = self._catches_by_en.get(en_key)
        if entry:
            return catch_display_name(entry, self._prefs.get("language", "ru"))
        return en_key

    def _build_catch_checkboxes(self):
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks = {}
        self._visible = {}
        selected = set(self._prefs.get("Catch List", []))
        stretch = self._list_layout.takeAt(self._list_layout.count() - 1)
        self._suspend = True
        try:
            for en_key in self._catch_names:
                check = QCheckBox(self.display_name_for_en(en_key), self._list_host)
                check.setChecked(en_key in selected)
                self._mark_selected(check)
                check.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                check.toggled.connect(lambda _checked, k=en_key: self._on_toggled(k))
                self._list_layout.addWidget(check)
                self._checks[en_key] = check
                self._visible[en_key] = True
        finally:
            self._suspend = False
        if stretch is not None:
            self._list_layout.addItem(stretch)
        else:
            self._list_layout.addStretch(1)

    def _on_search_text(self, _text=""):
        self._search_timer.start()

    def _apply_filter(self):
        query = self._search.text().strip().lower()
        for en_key, check in self._checks.items():
            name = self.display_name_for_en(en_key).lower()
            visible = (not query) or query in name or query in en_key.lower()
            if self._visible.get(en_key) == visible:
                continue
            self._visible[en_key] = visible
            check.setVisible(visible)

    def _select_preset(self, category):
        keys = list(self._statistics.get(category, {}).keys())
        self.set_selected_en_keys(keys)
        self._prefs["Catch List"] = self.get_selected_en_keys()
        self._save_prefs()
        self.selection_changed.emit()

    @staticmethod
    def _mark_selected(check):
        # Color for the checked row comes from one QSS rule
        # (QCheckBox[selected="true"]) instead of per-row inline styles.
        selected = check.isChecked()
        if check.property("selected") != selected:
            check.setProperty("selected", selected)
            style = check.style()
            style.unpolish(check)
            style.polish(check)

    def _on_toggled(self, en_key):
        check = self._checks.get(en_key)
        if check is not None:
            self._mark_selected(check)
        if self._suspend:
            return
        self._prefs["Catch List"] = self.get_selected_en_keys()
        self._save_prefs()
        self.selection_changed.emit()

    def set_controls_enabled(self, enabled: bool):
        self._clear.setEnabled(enabled)
        self._select_all.setEnabled(enabled)
        self._preset_fish.setEnabled(enabled)
        self._preset_quest.setEnabled(enabled)
        self._preset_crates.setEnabled(enabled)
        self._search.setEnabled(enabled)
        for check in self._checks.values():
            check.setEnabled(enabled)

    def refresh_labels(self):
        for en_key, check in self._checks.items():
            check.setText(self.display_name_for_en(en_key))
            self._mark_selected(check)
        self._apply_filter()

    def set_selected_en_keys(self, en_keys):
        selected = set(en_keys)
        self._suspend = True
        try:
            for en_key, check in self._checks.items():
                check.setChecked(en_key in selected)
                self._mark_selected(check)
        finally:
            self._suspend = False

    def get_selected_en_keys(self):
        return [en_key for en_key, check in self._checks.items() if check.isChecked()]

    def clear_clicked(self):
        self.set_selected_en_keys([])
        self._prefs["Catch List"] = []
        self._save_prefs()

    def select_all_clicked(self):
        self.set_selected_en_keys(self._catch_names)
        self._prefs["Catch List"] = list(self._catch_names)
        self._save_prefs()
        self.select_all_done.emit(len(self._catch_names))
        return len(self._catch_names)

    def _on_select_all(self):
        self.select_all_clicked()
