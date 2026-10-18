"""Sentry bridge is removable: no DSN and no sentry-sdk must not change behavior."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

from wayfinder.demo_site import build_snapshot
from wayfinder.model_backend import model_rank, model_summary
from wayfinder.models import Goal, PageState
from wayfinder.observability import export_event, trace_event
from wayfinder.orchestrator import create_session, handle_turn


def test_sentry_helpers_noop_without_init(monkeypatch):
    monkeypatch.delenv("WAYFINDER_SENTRY_DSN", raising=False)
    import wayfinder.observability as obs

    importlib.reload(obs)
    assert not obs.sentry_initialized()

    snapshot = build_snapshot(PageState())
    goal = Goal(text="Plan a trip", constraints=[])
    baseline_summary = model_summary(snapshot, goal, "standard")
    baseline_rank = model_rank(snapshot.controls, [], goal, "standard")

    with obs.sentry_turn(trace_id="t", session_id="s", turn_id="u"):
        with obs.sentry_model_span():
            obs.sentry_capture_exception(RuntimeError("probe"))

    event = trace_event(
        trace_id="trace",
        session_id="sess",
        turn_id="turn",
        source="interpreter",
        action="build_wir",
        status="ok",
        started=__import__("time").perf_counter(),
    )
    assert export_event(event) is False

    view = create_session()
    turn = handle_turn(view["session_id"], "what is here")
    assert turn["speech"]
    assert model_summary(snapshot, goal, "standard") == baseline_summary
    assert model_rank(snapshot.controls, [], goal, "standard") == baseline_rank


def test_api_boots_without_sentry_sdk(monkeypatch):
    monkeypatch.delenv("WAYFINDER_SENTRY_DSN", raising=False)
    env = {k: v for k, v in os.environ.items() if k != "WAYFINDER_SENTRY_DSN"}
    env["WAYFINDER_MEMORY_PATH"] = ":memory:"
    code = """
import sys
sys.modules["sentry_sdk"] = None
import wayfinder.api
from wayfinder.orchestrator import create_session, handle_turn
v = create_session()
r = handle_turn(v["session_id"], "what is here")
assert r["speech"]
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=os.getcwd())
    assert result.returncode == 0, result.stderr or result.stdout


def test_dsn_without_sentry_sdk_does_not_crash(monkeypatch):
    monkeypatch.setenv("WAYFINDER_SENTRY_DSN", "https://example.invalid/1")
    env = {k: v for k, v in os.environ.items()}
    env["WAYFINDER_MEMORY_PATH"] = ":memory:"
    code = """
import sys
sys.modules["sentry_sdk"] = None
import importlib
import wayfinder.observability as obs
importlib.reload(obs)
import wayfinder.api
from wayfinder.orchestrator import create_session, handle_turn
v = create_session()
assert handle_turn(v["session_id"], "stop")["speech"]
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=os.getcwd())
    assert result.returncode == 0, result.stderr or result.stdout


def test_mark_sentry_active_still_noops_capture_without_sdk(monkeypatch):
    monkeypatch.delenv("WAYFINDER_SENTRY_DSN", raising=False)
    import wayfinder.observability as obs

    importlib.reload(obs)
    obs.mark_sentry_active()
    assert obs.sentry_initialized()
    obs.sentry_capture_exception(ValueError("ignored"))
    with obs.sentry_turn(trace_id="a", session_id="b", turn_id="c"):
        pass
    importlib.reload(obs)
