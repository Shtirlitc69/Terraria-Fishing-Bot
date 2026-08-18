from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QAction, QKeySequence, QTextCursor
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
    def __init__(self, text="", parent=None, accent="#3b8ed0"):
        super().__init__(text, parent)
        self._accent = QColor(accent)
        self._bg = QColor(accent)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_style()

    def set_accent(self, hex_color: str):
        self._accent = QColor(hex_color)
        self._stop_anim()
        self._bg = QColor(self._accent)
        self._apply_style()

    def _on_anim_value(self, value):
        if isinstance(value, QColor):
            self._bg = QColor(value)
            self._apply_style()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate_to(self._accent.lighter(118))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_to(self._accent)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._animate_to(self._accent.darker(125))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.underMouse() and self.isEnabled():
            self._animate_to(self._accent.lighter(118))
        else:
            self._animate_to(self._accent)
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._apply_style()

    def _stop_anim(self):
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()

    def _animate_to(self, color: QColor):
        if not self.isEnabled():
            return
        self._stop_anim()
        self._anim.setStartValue(self._bg)
        self._anim.setEndValue(QColor(color))
        self._anim.start()

    def _apply_style(self):
        if not self.isEnabled():
            self.setStyleSheet(
                "QPushButton { background-color: #3a3a3a; color: #888888; border: none; padding: 8px 12px; }"
            )
            return
        name = self._bg.name()
        self.setStyleSheet(
            f"QPushButton {{ background-color: {name}; color: #ffffff; "
            f"border: none; padding: 8px 12px; }}"
        )


class TitleBar(QWidget):
    close_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(36)

        self._title = QLabel(i18n.t("app_title"), self)
        self._title.setObjectName("titleBarLabel")

        self._min_btn = QPushButton("–", self)
        self._min_btn.setObjectName("titleBarMin")
        self._min_btn.setFixedSize(36, 28)
        self._min_btn.clicked.connect(self.minimize_requested.emit)

        self._close_btn = QPushButton("×", self)
        self._close_btn.setObjectName("titleBarClose")
        self._close_btn.setFixedSize(36, 28)
        self._close_btn.clicked.connect(self.close_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(4)
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
