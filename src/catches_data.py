import json
from pathlib import Path


def load_catches(resource_path_fn):
    path = resource_path_fn(Path("data") / "catches.json")
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    by_en = {entry["en"]: entry for entry in entries}
    by_id = {int(entry["id"]): entry for entry in entries if "id" in entry}
    names = [entry["en"] for entry in entries]
    display_to_en = {}
    for entry in entries:
        display_to_en[entry["en"]] = entry["en"]
        display_to_en[entry["ru"]] = entry["en"]
        sonar = entry.get("sonar_ru")
        if sonar:
            display_to_en[sonar] = entry["en"]
    return entries, names, by_en, display_to_en, by_id


def catch_display_name(entry, lang: str) -> str:
    if lang == "ru":
        return entry["ru"]
    return entry["en"]


def resolve_en_key(text: str, display_to_en: dict, by_en: dict):
    text = text.strip()
    if not text:
        return None
    if text in by_en:
        return text
    return display_to_en.get(text)


def ids_for_en_keys(en_keys, by_en: dict):
    ids = []
    for key in en_keys:
        entry = by_en.get(key)
        if entry and "id" in entry:
            ids.append(int(entry["id"]))
    return ids
