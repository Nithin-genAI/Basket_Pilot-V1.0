"""Small SQLite session store; secrets and raw address data are never persisted."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

TTL_SECONDS = 60 * 60

_TRANSITIONS = {
    ("IDLE", "list"): "COLLECTING_LIST",
    ("COLLECTING_LIST", "parsed"): "NEED_ADDRESS",
    ("NEED_ADDRESS", "address"): "SEARCHING",
    ("SEARCHING", "planned"): "PLAN_READY",
    ("PLAN_READY", "cart_confirm"): "CART_BUILDING",
    ("CART_BUILDING", "preview"): "PREVIEW_READY",
    ("FAILED", "list"): "COLLECTING_LIST",
    ("PLACED", "list"): "COLLECTING_LIST",
    ("PREVIEW_READY", "list"): "COLLECTING_LIST",
}


def transition(current: str, event: str) -> str:
    """Return the next README 5.3 state or fail on an invalid transition."""
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError as exc:
        raise ValueError(f"invalid session transition: {current} + {event}") from exc


class SessionStore:
    def __init__(self, path: str | None = None, ttl_seconds: int = TTL_SECONDS) -> None:
        self.path = Path(path or os.environ.get("BP_SESSION_DB", "data/sessions.sqlite3"))
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, session_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE updated < ?", (now - self.ttl_seconds,))
            row = db.execute("SELECT payload FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, session_id: str, state: dict[str, Any]) -> None:
        safe = {k: v for k, v in state.items() if k not in {"address", "address_id", "preview_token", "secret", "otp", "cvv"}}
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(session_id, payload, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
                (session_id, json.dumps(safe, sort_keys=True), time.time()),
            )

    def delete(self, session_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
