"""User account storage and role/permission helpers."""
import json
from pathlib import Path

class UserStore:
    def __init__(self, path): self.path=Path(path)
    def load(self):
        try: return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception: return {}
    def save(self, users):
        self.path.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")

def permissions_for(user, permissions):
    return list(permissions) if user and user.get("role")=="admin" else list((user or {}).get("permissions", []))
