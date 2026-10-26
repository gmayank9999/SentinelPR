"""Execution events: one append-only, persisted, streamable log per analysis run.

Event schema (``sentinelpr.event/1``)::

    id        "<run_id>:<seq>"   stable across reconnects and reloads
    run_id    the analysis run
    seq       1, 2, 3 ... within the run (SSE ``Last-Event-ID`` resumes after this)
    ts        unix time the event was recorded
    stage     the graph node it belongs to (intake, index, guard_in, ..., verify, ...) or "run"
    kind      run_queued | run_started | run_completed | run_failed | run_cancelled |
              stage_started | stage_completed | stage_failed | stage_skipped | substep | llm_call | log
    status    running | completed | failed | skipped | info | warn | queued | cancelled
    message   one human-readable line
    data      structured payload (secrets redacted)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sentinel.guards.input import redact_secrets

SCHEMA = "sentinelpr.event/1"
SENSITIVE_KEYS = ("token", "secret", "password", "passwd", "api_key", "apikey", "authorization", "credential")
MAX_STRING = 4000


def redact(value, depth: int = 0):
    """Redact secrets from anything about to be stored or sent to a browser."""
    if depth > 8:
        return "[truncated]"
    if isinstance(value, str):
        return redact_secrets(value)[:MAX_STRING]
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in SENSITIVE_KEYS) and isinstance(v, str) else redact(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, depth + 1) for v in list(value)[:500]]
    return value


class EventLog:
    """Thread-safe event list for one run. Parallel pipeline branches append concurrently."""

    def __init__(self, run_id: str, path: Path | None = None, events: list[dict] | None = None):
        self.run_id = run_id
        self.path = path
        self._events: list[dict] = list(events or [])
        self._cond = threading.Condition()
        self._closed = False
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    # writing -----------------------------------------------------------------
    def emit(self, stage: str, kind: str, status: str, message: str, data: dict | None = None) -> dict:
        with self._cond:
            seq = len(self._events) + 1
            event = {"schema": SCHEMA, "id": f"{self.run_id}:{seq}", "run_id": self.run_id, "seq": seq, "ts": time.time(),
                     "stage": stage, "kind": kind, "status": status, "message": redact(message), "data": redact(data or {})}
            self._events.append(event)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, default=str) + "\n")
            self._cond.notify_all()
        return event

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    # reading -----------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed

    def since(self, seq: int = 0) -> list[dict]:
        with self._cond:
            return self._events[seq:]

    def wait(self, seq: int, timeout: float) -> list[dict]:
        """Block until there are events after ``seq`` (or the log closes or the timeout passes)."""
        with self._cond:
            if len(self._events) <= seq and not self._closed:
                self._cond.wait(timeout)
            return self._events[seq:]

    # persistence ---------------------------------------------------------------
    @classmethod
    def load(cls, run_id: str, path: Path) -> "EventLog":
        events: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn last line after a crash
        log = cls(run_id, path, events)
        log._closed = True
        return log
