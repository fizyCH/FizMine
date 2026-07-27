"""Persistent panel and environment settings."""
import json
from pathlib import Path

class JsonSettings:
    def __init__(self, path, defaults=None): self.path=Path(path); self.defaults=defaults or {}
    def load(self):
        try: data=json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception: data={}
        return {**self.defaults, **(data if isinstance(data,dict) else {})}
    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
