"""Structured trace events. Hidden chain-of-thought is not stored."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

from wayfinder.contracts import PROTOCOL_VERSION, WIR_VERSION

_SECRET_KEYS = {"password", "secret", "token", "authorization", "cookie", "api_key"}

# Proposal 11.1 fields. firmware_version stays null until a flashed artifact exists.
TRACE_FIELDS = (
    "ts_ms",
    "session_id",
    "turn_id",
    "trace_id",
    "component",
    "schema_version",
    "protocol_version",
    "firmware_version",
    "input_hash",
    "output_hash",
    "action_type",
    "evidence_ref",
    "risk_class",
    "confirmation_outcome",
    "device_id",
    "sequence",
    "transport_status",
    "latency_ms",
    "result_class",
    "fallback",
    "redaction_state",
)


def redact(value: Any, state: dict[str, bool] | None = None) -> Any:
    if state is None:
        state = {"redacted": False}
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[redacted]"
                state["redacted"] = True
            else:
                cleaned[key] = redact(item, state)
        return cleaned
    if isinstance(value, list):
        return [redact(item, state) for item in value]
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _from_detail(detail: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in detail:
            return detail[key]
    return None


def trace_event(
    *,
    trace_id: str,
    session_id: str,
    turn_id: str,
    source: str,
    action: str,
    status: str,
    started: float,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    redaction = {"redacted": False}
    cleaned = redact(detail or {}, redaction)
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "ts_ms": int(time.time() * 1000),
        "source": source,
        "component": source,
        "action": action,
        "action_type": action,
        "status": status,
        "result_class": status,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "schema_version": WIR_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "firmware_version": None,
        "input_hash": _digest(cleaned),
        "output_hash": _digest({"action": action, "status": status}),
        "evidence_ref": _from_detail(cleaned, "evidence_ref", "evidence"),
        "risk_class": _from_detail(cleaned, "risk_class"),
        "confirmation_outcome": _from_detail(cleaned, "confirmation_outcome"),
        "device_id": _from_detail(cleaned, "device_id"),
        "sequence": _from_detail(cleaned, "sequence"),
        "transport_status": _from_detail(cleaned, "transport_status"),
        "fallback": _from_detail(cleaned, "fallback"),
        "redaction_state": "redacted" if redaction["redacted"] else "clean",
        "detail": cleaned,
        "provider": None,
        "model": None,
    }


_sink_lock = threading.Lock()


def export_event(event: dict[str, Any]) -> bool:
    """Append one redacted event as a JSON line when WAYFINDER_TRACE_FILE is set.

    This is a removable observability adapter. Nothing in the demo depends on it,
    and it is not Sentry. A collector such as Sentry Logs, a file shipper, or a
    notebook can read the file. Returns True when a line was written.
    """
    path = os.environ.get("WAYFINDER_TRACE_FILE", "").strip()
    if not path:
        return False
    line = json.dumps(redact(event), sort_keys=True, default=str)
    try:
        with _sink_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return False
    return True
