"""Semantic session memory. Raw audio and images are rejected."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class MemoryError(ValueError):
    pass


class MemoryCurator:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            """
            create table if not exists semantic_memory (
                id integer primary key,
                session_id text not null,
                created_ms integer not null,
                summary text not null
            )
            """
        )
        self._conn.commit()

    def add(self, session_id: str, summary: str) -> None:
        cleaned = " ".join(summary.split())
        if len(cleaned) > 500:
            raise MemoryError("summary_too_long")
        lowered = cleaned.lower()
        if "data:image" in lowered or "base64," in lowered or "raw_pcm" in lowered:
            raise MemoryError("raw_media_forbidden")
        with self._lock:
            self._conn.execute(
                "insert into semantic_memory (session_id, created_ms, summary) values (?, ?, ?)",
                (session_id, int(time.time() * 1000), cleaned),
            )
            self._conn.commit()

    def lines(self, session_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "select summary from semantic_memory where session_id = ? order by id",
                (session_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        self._conn.close()
