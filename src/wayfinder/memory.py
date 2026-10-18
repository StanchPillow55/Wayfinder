"""Semantic session memory. Raw audio and images are rejected."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class MemoryError(ValueError):
    pass


# The personalization bank holds preferences only. It never holds a diagnosis, a name,
# audio, or images. Anything outside this list is refused.
PREFERENCE_KEYS = {
    "density": {"brief", "standard", "full"},
    "branch_limit": {"3", "5"},
    "preferred_channels": None,  # comma list of channel ids, checked in clean_channels
}
CHANNEL_IDS = {"visual", "audio", "braille", "tactile", "glance", "keyboard", "voice", "camera", "switches"}


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
        self._conn.execute(
            "create table if not exists preferences (key text primary key, value text not null)"
        )
        self._conn.execute(
            """
            create table if not exists workspaces (
                name text primary key,
                updated_ms integer not null,
                body text not null
            )
            """
        )
        self._conn.commit()

    # ---- personalization bank: preferences only ----

    @staticmethod
    def clean_channels(value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items or any(item not in CHANNEL_IDS for item in items):
            raise MemoryError("unknown_channel")
        return ",".join(dict.fromkeys(items))

    def set_preference(self, key: str, value: str) -> None:
        if key not in PREFERENCE_KEYS:
            raise MemoryError("preference_not_allowed")
        value = str(value).strip().lower()
        if key == "preferred_channels":
            value = self.clean_channels(value)
        elif value not in (PREFERENCE_KEYS[key] or set()):
            raise MemoryError("preference_value_not_allowed")
        with self._lock:
            self._conn.execute(
                "insert into preferences (key, value) values (?, ?) "
                "on conflict(key) do update set value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def preferences(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("select key, value from preferences").fetchall()
        return {key: value for key, value in rows if key in PREFERENCE_KEYS}

    def reset_preferences(self) -> None:
        with self._lock:
            self._conn.execute("delete from preferences")
            self._conn.commit()

    # ---- named projects: a workspace that can be continued ----

    def save_workspace(self, name: str, body: dict) -> None:
        cleaned = " ".join(name.split()).lower()[:60]
        if not cleaned:
            raise MemoryError("project_name_required")
        text = json.dumps(body, sort_keys=True)
        lowered = text.lower()
        if "data:image" in lowered or "base64," in lowered or "raw_pcm" in lowered or len(text) > 20_000:
            raise MemoryError("raw_media_forbidden")
        with self._lock:
            self._conn.execute(
                "insert into workspaces (name, updated_ms, body) values (?, ?, ?) "
                "on conflict(name) do update set updated_ms = excluded.updated_ms, body = excluded.body",
                (cleaned, int(time.time() * 1000), text),
            )
            self._conn.commit()

    def workspaces(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("select name from workspaces order by updated_ms desc").fetchall()
        return [row[0] for row in rows]

    def load_workspace(self, name: str) -> dict | None:
        wanted = " ".join(name.split()).lower()
        if not wanted:
            return None
        names = self.workspaces()
        match = wanted if wanted in names else next(
            (item for item in names if wanted in item or item in wanted), None
        )
        if match is None:
            return None
        with self._lock:
            row = self._conn.execute("select body from workspaces where name = ?", (match,)).fetchone()
        if row is None:
            return None
        body = json.loads(row[0])
        body["name"] = match
        return body

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
