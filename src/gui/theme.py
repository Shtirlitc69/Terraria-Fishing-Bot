from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

ACCENTS = {
    "blue": "#3b8ed0",
    "dark-blue": "#1f538d",
    "green": "#2cc985",
}

_FALLBACK_QSS = """
QWidget {
    background-color: #1e1e1e;
    color: #e6e6e6;
    font-family: "Segoe UI";
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #1e1e1e;
}
QTabWidget::pane {
    border: 1px solid #3a3a3a;
    background: #252526;
}
QTabBar::tab {
    background: #2d2d2d;
    color: #e6e6e6;
    padding: 8px 14px;
    border: 1px solid #3a3a3a;
}
QTabBar::tab:selected {
    background: {accent};
    color: #ffffff;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background: #2b2b2b;
    color: #e6e6e6;
    border: 1px solid #3a3a3a;
    padding: 4px 8px;
    selection-background-color: {accent};
}
QCheckBox {
    color: #e6e6e6;
    spacing: 8px;
}
QScrollArea {
    border: none;
    background: #2b2b2b;
}
QPushButton {
    background-color: {accent};
    color: #ffffff;
    border: none;
    padding: 8px 12px;
}
QLabel#titleBarLabel {
    color: #e6e6e6;
    font-size: 13px;
    font-weight: 600;
}
"""


def accent_hex(key: str) -> str:
    return ACCENTS.get(str(key).lower(), ACCENTS["blue"])


def _shift(hex_color: str, lighter: int) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        color = QColor(ACCENTS["blue"])
    if lighter >= 100:
        return color.lighter(lighter).name()
    return color.darker(max(100, 200 - lighter)).name()


def load_qss_template() -> str:
    from gui.paths import resource_path

    candidate = resource_path(Path("gui") / "styles" / "dark.qss")
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    local = Path(__file__).resolve().parent / "styles" / "dark.qss"
    if local.exists():
        return local.read_text(encoding="utf-8")
    return _FALLBACK_QSS


def qss_for(accent_key: str) -> str:
    accent = accent_hex(accent_key)
    template = load_qss_template()
    return (
        template.replace("{accent_hover}", _shift(accent, 120))
        .replace("{accent_pressed}", _shift(accent, 80))
        .replace("{accent}", accent)
        .replace("{selected}", "#3dd68c")
    )


def apply_theme(app: QApplication, accent_key: str):
    app.setStyleSheet(qss_for(accent_key))
