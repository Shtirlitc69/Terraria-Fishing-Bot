import sys
from pathlib import Path


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def probe_log_path():
    return get_app_dir() / "projectile_probe.jsonl"


def debug_log_path():
    return get_app_dir() / "memory_bot_debug.log"


def events_log_path():
    return get_app_dir() / "fishing_events.jsonl"


def fishing_log_path():
    return get_app_dir() / "fishing_log.txt"


def layout_cache_path():
    return get_app_dir() / "bobber_layout.json"


def resource_path(relative):
    rel = Path(relative)
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", get_app_dir()))
        bundled = meipass / rel
        if bundled.exists():
            return bundled
        local = get_app_dir() / rel
        if local.exists():
            return local
    return get_app_dir() / rel
