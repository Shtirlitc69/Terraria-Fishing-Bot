from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from catches_data import catch_display_name

_CATEGORIES = ("Fish", "Quest Fish", "Usable Items", "Crates")
_TAB_KEYS = ("tab_fish", "tab_quest_fish", "tab_usable", "tab_crates")


class StatsTab(QWidget):
    def __init__(self, i18n, prefs, catches_by_en, statistics, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._prefs = prefs
        self._catches_by_en = catches_by_en
        self._statistics = statistics
        self._name_labels = []
        self._count_labels = []
        self._headers = []

        self._title = QLabel(self._i18n.t("statistics"))
        self._title.setObjectName("sectionTitle")

        self._tabs = QTabWidget(self)
        self._scrolls = []
        self._hosts = []
        for key in _TAB_KEYS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 8, 8, 8)
            page_layout.setSpacing(8)
            header = QLabel(self._i18n.t("num_catches"))
            header.setObjectName("hintLabel")
            self._headers.append(header)
            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(4, 4, 4, 4)
            host_layout.setSpacing(2)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(host)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            page_layout.addWidget(header)
            page_layout.addWidget(scroll, 1)
            self._tabs.addTab(page, self._i18n.t(key))
            self._scrolls.append(scroll)
            self._hosts.append(host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(self._title)
        layout.addWidget(self._tabs, 1)
        self.build_rows()

    def _lang(self):
        return self._prefs.get("language", "ru")

    def stat_display_name(self, en_key):
        entry = self._catches_by_en.get(en_key)
        if entry:
            return catch_display_name(entry, self._lang())
        return en_key

    def retranslate(self):
        self._title.setText(self._i18n.t("statistics"))
        for index, key in enumerate(_TAB_KEYS):
            self._tabs.setTabText(index, self._i18n.t(key))
        for header in self._headers:
            header.setText(self._i18n.t("num_catches"))
        self.update_counts()

    def build_rows(self):
        self._name_labels = []
        self._count_labels = []
        for cat_index, category in enumerate(_CATEGORIES):
            host = self._hosts[cat_index]
            layout = host.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            names = []
            counts = []
            mapping = self._statistics.get(category, {})
            for en_key, value in mapping.items():
                row = QWidget(host)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 2, 4, 2)
                name_label = QLabel(self.stat_display_name(en_key), row)
                name_label.setWordWrap(True)
                name_label.setAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                name_label.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
                )
                count_label = QLabel(str(value), row)
                count_label.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                row_layout.addWidget(name_label, 1)
                row_layout.addWidget(count_label, 0)
                layout.addWidget(row)
                names.append(name_label)
                counts.append(count_label)
            layout.addStretch(1)
            self._name_labels.append(names)
            self._count_labels.append(counts)

    def update_counts(self):
        if not self._name_labels:
            self.build_rows()
            return
        for cat_index, category in enumerate(_CATEGORIES):
            mapping = self._statistics.get(category, {})
            labels = self._name_labels[cat_index]
            counts = self._count_labels[cat_index]
            if len(labels) != len(mapping):
                self.build_rows()
                return
            for i, (en_key, value) in enumerate(mapping.items()):
                labels[i].setText(self.stat_display_name(en_key))
                counts[i].setText(str(value))
