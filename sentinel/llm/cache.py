"""Response cache keyed by (model, prompt hash).

Free tiers are rate limited and benchmark runs repeat prompts, so every completion is
cached on disk. With temperature 0 a cache hit is indistinguishable from a fresh call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path


class ResponseCache:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, payload TEXT)")
        self.conn.commit()

    @staticmethod
    def key(model: str, system: str, user: str, params: dict) -> str:
        blob = json.dumps({"model": model, "system": system, "user": user, "params": params}, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT payload FROM responses WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, payload: dict) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, json.dumps(payload)))
            self.conn.commit()
