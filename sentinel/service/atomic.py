"""Crash-safe JSON files that are written by one thread while others read them.

A plain write-then-rename is atomic on POSIX, but on Windows ``os.replace`` fails with
``PermissionError`` while any reader has the destination open, and the dashboard polls these
files constantly. Writes therefore go through a process-wide lock shared with the readers, and
retry briefly for readers outside this process.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()


def write_json(path: Path, data, retries: int = 40) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        for attempt in range(retries):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.025)


def read_json(path: Path, retries: int = 40):
    path = Path(path)
    for attempt in range(retries):
        try:
            with _LOCK:
                return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(0.025)
