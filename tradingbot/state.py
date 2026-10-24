import json
import os
from pathlib import Path


class StateStore:
    """Speichert den Bot-Zustand atomar als JSON, damit ein Neustart nichts verliert."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)
