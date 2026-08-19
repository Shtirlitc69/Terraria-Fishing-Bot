import json
import re

WATCH_ALL = "all"
WATCH_CRATE = "crate"
AUTO_DRINK_WATCH_MODES = (WATCH_ALL, WATCH_CRATE)

DEFAULT_PREFERENCES = {
    "Catch List": [],
    "auto drink": False,
    "auto_drink_watch": WATCH_ALL,
    "Color Theme": "blue",
    "quick_buff_key": "b",
    "language": "ru",
    "window_geometry": "1100x680",
    "cast_aim": None,
    "projectile_probe": False,
    "window_mode": "normal",
}

_STALE_KEYS = (
    "grayscale",
    "confidence",
    "ui_scaling",
    "catch_all",
    "remember list",
)

_GEOMETRY_RE = re.compile(
    r"^(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?$"
)
_WINDOW_MODES = ("normal", "frameless", "fullscreen")


def normalize_cast_aim(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or x > 20000 or y > 20000:
        return None
    return [x, y]


def normalize_quick_buff_key(key):
    key = str(key).strip().lower()
    if len(key) == 1 and key.isalnum():
        return key
    return "b"


def normalize_auto_drink_watch(value):
    key = str(value or "").strip().lower()
    if key in AUTO_DRINK_WATCH_MODES:
        return key
    return WATCH_ALL


def _parse_offset(raw):
    if raw.startswith("+"):
        return int(raw[1:] or "0")
    return int(raw)


def parse_tk_geometry(value):
    if not isinstance(value, str) or not value.strip():
        return None
    match = _GEOMETRY_RE.match(value.strip())
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if match.group(3) is None:
        return width, height, None, None
    return width, height, _parse_offset(match.group(3)), _parse_offset(match.group(4))


def format_window_geometry(width, height, x=None, y=None):
    if x is None or y is None:
        return f"{int(width)}x{int(height)}"
    return f"{int(width)}x{int(height)}{int(x):+d}{int(y):+d}"


def load_preferences(path):
    with open(path, encoding="utf-8") as f:
        prefs = json.load(f)
    for key, value in DEFAULT_PREFERENCES.items():
        prefs.setdefault(key, value)
    for key in _STALE_KEYS:
        prefs.pop(key, None)
    prefs["quick_buff_key"] = normalize_quick_buff_key(prefs["quick_buff_key"])
    prefs["auto_drink_watch"] = normalize_auto_drink_watch(
        prefs.get("auto_drink_watch")
    )
    prefs["cast_aim"] = normalize_cast_aim(prefs.get("cast_aim"))
    if prefs.get("language") not in ("en", "ru"):
        prefs["language"] = "en"
    if prefs.get("window_mode") not in _WINDOW_MODES:
        prefs["window_mode"] = "normal"
    theme = str(prefs.get("Color Theme", "blue")).lower()
    if theme not in ("blue", "dark-blue", "green"):
        theme = "blue"
    prefs["Color Theme"] = theme
    return prefs


def save_preferences(path, prefs):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=4, ensure_ascii=False)
