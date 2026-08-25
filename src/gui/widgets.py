from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class AnimatedButton(QPushButton):
    """Flat accent button.

    Hover/pressed/disabled states come from the global stylesheet
    (M3 state layers), so there is no per-frame setStyleSheet churn.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class SecondaryButton(AnimatedButton):
    """Quiet action (presets, clear/select all) on a tonal surface."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("secondaryButton")


class TitleBar(QWidget):
    close_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(40)

        self._title = QLabel(i18n.t("app_title"), self)
        self._title.setObjectName("titleBarLabel")

        self._min_btn = QPushButton("–", self)
        self._min_btn.setObjectName("titleBarMin")
        self._min_btn.setFixedSize(40, 32)
        self._min_btn.clicked.connect(self.minimize_requested.emit)

        self._close_btn = QPushButton("×", self)
        self._close_btn.setObjectName("titleBarClose")
        self._close_btn.setFixedSize(40, 32)
        self._close_btn.clicked.connect(self.close_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._title, 1)
        layout.addWidget(self._min_btn, 0)
        layout.addWidget(self._close_btn, 0)

    def retranslate(self):
        self._title.setText(self._i18n.t("app_title"))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child in (self._min_btn, self._close_btn):
                super().mousePressEvent(event)
                return
            frame = self.window().frameGeometry().topLeft()
            self._drag_offset = event.globalPosition().toPoint() - frame
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class FishingLog(QPlainTextEdit):
    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self.setObjectName("fishingLog")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        copy_seq = QKeySequence(QKeySequence.StandardKey.Copy)
        select_seq = QKeySequence(QKeySequence.StandardKey.SelectAll)
        self._copy_action = QAction(self)
        self._copy_action.setShortcuts([copy_seq, QKeySequence(QKeySequence.StandardKey.Copy)])
        self._copy_action.triggered.connect(self.copy_selection)
        self.addAction(self._copy_action)
        self._select_action = QAction(self)
        self._select_action.setShortcut(select_seq)
        self._select_action.triggered.connect(self.select_all_log)
        self.addAction(self._select_action)
        self.retranslate()

    def retranslate(self):
        self._copy_action.setText(self._i18n.t("log_copy"))
        self._select_action.setText(self._i18n.t("log_select_all"))

    def append_log(self, text: str):
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def copy_selection(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            QApplication.clipboard().setText(cursor.selectedText().replace("\u2029", "\n"))

    def copy_all(self):
        QApplication.clipboard().setText(self.toPlainText())

    def select_all_log(self):
        self.selectAll()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction(self._copy_action)
        menu.addAction(self._select_action)
        menu.exec(event.globalPos())
