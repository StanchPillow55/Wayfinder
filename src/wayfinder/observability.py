"""Structured trace events. Hidden chain-of-thought is not stored."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from contextvars import ContextVar
from typing import Any, Iterator

_STAGE_SOURCES = frozenset({"interpreter", "ranker", "router", "policy", "executor", "verifier"})

_sentry_active = False
_turn_transaction: ContextVar[Any | None] = ContextVar("wayfinder_sentry_turn", default=None)

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


def sentry_initialized() -> bool:
    """True only after api.py successfully called sentry_sdk.init."""
    return _sentry_active


def mark_sentry_active() -> None:
    """Called from api.py after sentry_sdk.init succeeds."""
    global _sentry_active
    _sentry_active = True


@contextlib.contextmanager
def sentry_turn(*, trace_id: str, session_id: str, turn_id: str) -> Iterator[None]:
    """One performance transaction per orchestrator turn. No-op without Sentry."""
    if not _sentry_active:
        yield
        return
    try:
        import sentry_sdk
    except ImportError:
        yield
        return
    with sentry_sdk.start_transaction(op="wayfinder.turn", name="wayfinder.turn") as transaction:
        transaction.set_tag("trace_id", trace_id)
        transaction.set_tag("session_id", session_id)
        transaction.set_tag("turn_id", turn_id)
        token = _turn_transaction.set(transaction)
        try:
            yield
        finally:
            _turn_transaction.reset(token)


@contextlib.contextmanager
def sentry_model_span() -> Iterator[None]:
    """Span for removable model summary and rank calls."""
    if not _sentry_active:
        yield
        return
    try:
        import sentry_sdk
    except ImportError:
        yield
        return
    with sentry_sdk.start_span(op="wayfinder.model", name="wayfinder.model"):
        yield


def sentry_capture_exception(exc: BaseException) -> None:
    """Report an exception without changing local fallback behavior."""
    if not _sentry_active:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        sentry_sdk.capture_exception(exc)
    except Exception:
        return


def _bridge_sentry_event(event: dict[str, Any]) -> None:
    if not _sentry_active:
        return
    source = str(event.get("source") or event.get("component") or "")
    if source not in _STAGE_SOURCES:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    action = str(event.get("action") or event.get("action_type") or "stage")
    status = str(event.get("status") or event.get("result_class") or "")
    parent = _turn_transaction.get()
    span_cm: Any
    if parent is not None:
        span_cm = parent.start_child(op=f"wayfinder.{source}", description=action)
    else:
        span_cm = sentry_sdk.start_span(op=f"wayfinder.{source}", description=action)
    try:
        with span_cm as span:
            span.set_tag("status", status)
            span.set_tag("trace_id", event.get("trace_id", ""))
            if event.get("latency_ms") is not None:
                span.set_data("latency_ms", event["latency_ms"])
    except Exception:
        return


def export_event(event: dict[str, Any]) -> bool:
    """Append one redacted event as a JSON line when WAYFINDER_TRACE_FILE is set.

    This is a removable observability adapter. Nothing in the demo depends on it,
    and it is not Sentry. A collector such as Sentry Logs, a file shipper, or a
    notebook can read the file. Returns True when a line was written.
    """
    _bridge_sentry_event(event)
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
