import json
from pathlib import Path


class I18n:
    def __init__(self, resource_path_fn):
        self._resource_path = resource_path_fn
        self.lang = "en"
        self._strings = {}

    def load(self, lang: str):
        self.lang = lang if lang in ("en", "ru") else "en"
        path = self._resource_path(Path("locale") / f"{self.lang}.json")
        with open(path, encoding="utf-8") as f:
            self._strings = json.load(f)

    def t(self, key: str, **kwargs) -> str:
        text = self._strings.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text
