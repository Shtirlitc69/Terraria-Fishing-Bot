from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

# Design tokens (Refactoring UI grey scale / M3 surface roles).
# Single source of truth: widgets and QSS must not hardcode hex values.
GREYS = {
    "grey-900": "#17191c",
    "grey-800": "#1b1d21",
    "grey-700": "#22252a",
    "grey-600": "#282c33",
    "grey-500": "#33373e",
    "grey-400": "#4a4f58",
    "grey-300": "#6e747d",
    "grey-200": "#9aa0a6",
    "grey-100": "#c8ccd1",
    "grey-50": "#e8eaed",
}

ACCENTS = {
    "blue": "#3b8ed0",
    "dark-blue": "#1f538d",
    "green": "#2cc985",
}

SELECTED = "#3dd68c"

TOKENS = {
    **GREYS,
    # M3-like tonal roles: depth comes from surface tone, not from borders.
    "surface": GREYS["grey-800"],
    "panel": GREYS["grey-700"],
    "field": GREYS["grey-600"],
    "on_surface": GREYS["grey-50"],
    "on_surface_variant": GREYS["grey-200"],
    "outline": GREYS["grey-400"],
    "outline_variant": GREYS["grey-500"],
    "disabled_bg": GREYS["grey-500"],
    "disabled_fg": GREYS["grey-300"],
    "selected": SELECTED,
    "radius_s": "4px",
    "radius_m": "6px",
}

_FALLBACK_QSS = """
QWidget {
    background-color: {surface};
    color: {on_surface};
    font-family: "Segoe UI";
    font-size: 13px;
}
QPushButton {
    background-color: {accent};
    color: #ffffff;
    border: none;
    border-radius: {radius_m};
}
"""


def accent_hex(key: str) -> str:
    return ACCENTS.get(str(key).lower(), ACCENTS["blue"])


def on_accent_hex(hex_color: str) -> str:
    """Pick black or white text for WCAG-ish contrast on an accent."""
    color = QColor(hex_color)
    if not color.isValid():
        return "#ffffff"
    luminance = (
        0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
    ) / 255.0
    return "#1b1d21" if luminance > 0.62 else "#ffffff"


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
    values = {
        **TOKENS,
        "accent": accent,
        # M3 state layers, resolved once per theme switch instead of per frame.
        "accent_hover": _shift(accent, 118),
        "accent_pressed": _shift(accent, 82),
        "hover_layer": "rgba(255, 255, 255, 14)",
        "pressed_layer": "rgba(255, 255, 255, 26)",
        "focus_ring": f"1px solid {_shift(accent, 130)}",
        "selection_bg": _shift(accent, 140),
        "on_accent": on_accent_hex(accent),
        "icons_dir": (Path(__file__).resolve().parent / "styles").as_posix(),
    }
    template = load_qss_template()
    qss = template
    for key, value in values.items():
        qss = qss.replace("{" + key + "}", value)
    return qss


def apply_theme(app: QApplication, accent_key: str):
    app.setStyleSheet(qss_for(accent_key))
