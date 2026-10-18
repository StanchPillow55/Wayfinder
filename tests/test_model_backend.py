"""FR-014 model backend: selection, validation, and deterministic fallback."""

import io
import json
import urllib.error
import urllib.request

import pytest

from wayfinder.demo_site import build_snapshot
from wayfinder.model_backend import (
    TIMEOUT_S,
    ASIOneBackend,
    DeterministicBackend,
    ModelCallError,
    OllamaBackend,
    _interpreter_summary,
    _ollama_reachable,
    model_rank,
    model_summary,
    resolve_backend,
)
from wayfinder.models import Control, Goal, PageState
from wayfinder.orchestrator import create_session, handle_turn
from wayfinder.ranker import rank


def _control(control_id: str, priority: int) -> Control:
    return Control(
        id=control_id,
        label=f"Label {control_id}",
        rationale="Grounded on the page.",
        capability="reader.explain",
        risk_class="read",
        semantic_target=f"stamp|{control_id}",
        expected_postcondition={"explained": True},
        informational=True,
        preview="Reads this control.",
        priority=priority,
    )


def _controls(count: int = 4) -> list[Control]:
    return [_control(f"c{index}", 100 - index) for index in range(count)]


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _ollama_body(content: str) -> _Response:
    body = json.dumps({"message": {"role": "assistant", "content": content}}).encode()
    return _Response(body)


def _asione_body(content: str) -> _Response:
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
    return _Response(body)


@pytest.fixture
def harbor():
    snapshot = build_snapshot(PageState())
    goal = Goal(text="Plan a step-free trip", constraints=["step_free"])
    return snapshot, goal


def test_resolve_backend_defaults_and_explicit_modes(monkeypatch):
    monkeypatch.delenv("WAYFINDER_MODEL", raising=False)
    monkeypatch.delenv("WAYFINDER_ASIONE_KEY", raising=False)
    assert isinstance(resolve_backend(), DeterministicBackend)

    monkeypatch.setenv("WAYFINDER_MODEL", "off")
    assert isinstance(resolve_backend(), DeterministicBackend)

    monkeypatch.setenv("WAYFINDER_MODEL", "OFF")
    assert isinstance(resolve_backend(), DeterministicBackend)

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    assert isinstance(resolve_backend(), OllamaBackend)

    monkeypatch.setenv("WAYFINDER_MODEL", "asione")
    backend = resolve_backend()
    assert isinstance(backend, ASIOneBackend)
    assert backend.model == "asi1-mini"

    monkeypatch.setenv("WAYFINDER_MODEL", "something-else")
    assert isinstance(resolve_backend(), DeterministicBackend)


def test_resolve_auto_prefers_asione_then_ollama_then_deterministic(monkeypatch):
    monkeypatch.setenv("WAYFINDER_MODEL", "auto")
    monkeypatch.setenv("WAYFINDER_ASIONE_KEY", "test-key")
    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: False)
    assert isinstance(resolve_backend(), ASIOneBackend)

    monkeypatch.setenv("WAYFINDER_ASIONE_KEY", "  ")
    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: True)
    assert isinstance(resolve_backend(), OllamaBackend)

    monkeypatch.delenv("WAYFINDER_ASIONE_KEY", raising=False)
    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: False)
    assert isinstance(resolve_backend(), DeterministicBackend)

    monkeypatch.setenv("WAYFINDER_ASIONE_KEY", "test-key")
    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: True)
    assert isinstance(resolve_backend(), ASIOneBackend)


def test_ollama_rank_model_and_asione_request_shape(monkeypatch):
    seen: dict = {}

    def capture(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["auth"] = request.get_header("Authorization")
        seen["payload"] = json.loads(request.data.decode())
        if "11434" in request.full_url:
            return _ollama_body(json.dumps({"ids": ["c1"]}))
        return _asione_body(json.dumps({"summary": "Remote summary."}))

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    monkeypatch.setenv("WAYFINDER_OLLAMA_MODEL", "llama3.1:latest")
    monkeypatch.setenv("WAYFINDER_OLLAMA_RANK_MODEL", "llama3.2:1b")
    controls = _controls(2)
    ids = OllamaBackend().rank_ids(controls, [], Goal(text="go"), "standard", 3)
    assert ids == ["c1"]
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["timeout"] == TIMEOUT_S == 6
    assert seen["payload"]["model"] == "llama3.2:1b"
    assert seen["payload"]["format"] == "json"
    assert seen["payload"]["stream"] is False

    monkeypatch.delenv("WAYFINDER_OLLAMA_RANK_MODEL", raising=False)
    OllamaBackend().rank_ids(controls, [], Goal(text="go"), "standard", None)
    assert seen["payload"]["model"] == "llama3.1:latest"

    monkeypatch.setenv("WAYFINDER_ASIONE_KEY", "secret-key")
    monkeypatch.setenv("WAYFINDER_ASIONE_BASE", "https://example.test/v1/")
    monkeypatch.setenv("WAYFINDER_ASIONE_MODEL", "asi1-mini")
    text = ASIOneBackend().summary(build_snapshot(PageState()), Goal(), "brief")
    assert text == "Remote summary."
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key"
    assert seen["timeout"] == 6
    assert seen["payload"]["model"] == "asi1-mini"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert "Ignore previous instructions" not in seen["payload"]["messages"][1]["content"]


def test_model_rank_drops_hallucinated_ids_and_keeps_grounded_objects(monkeypatch, harbor):
    snapshot, goal = harbor
    known = {control.id: control for control in snapshot.controls}

    class _Mixed:
        def summary(self, snapshot, goal, density):
            raise AssertionError("summary should not run")

        def rank_ids(self, controls, constraints, goal, density, limit=None):
            first = controls[-1].id
            return ["not-on-the-page", first, "also-invented", first, 3]

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    monkeypatch.setattr("wayfinder.model_backend.resolve_backend", lambda: _Mixed())
    chosen = model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)
    assert [control.id for control in chosen] == [snapshot.controls[-1].id]
    assert chosen[0] is known[snapshot.controls[-1].id]
    assert chosen[0].label == snapshot.controls[-1].label
    assert chosen[0].semantic_target == snapshot.controls[-1].semantic_target


def test_model_rank_falls_back_when_ids_are_empty_or_invalid(monkeypatch, harbor):
    snapshot, goal = harbor
    expected = [control.id for control in rank(snapshot.controls, goal.constraints, "standard", 5)]

    class _Empty:
        def rank_ids(self, controls, constraints, goal, density, limit=None):
            return ["hallucinated-only", "still-fake"]

    class _Broken:
        def rank_ids(self, controls, constraints, goal, density, limit=None):
            raise ModelCallError("not_json")

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    monkeypatch.setattr("wayfinder.model_backend.resolve_backend", lambda: _Empty())
    assert [control.id for control in model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)] == expected

    monkeypatch.setattr("wayfinder.model_backend.resolve_backend", lambda: _Broken())
    assert [control.id for control in model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)] == expected


def test_model_rank_honors_density_and_branch_limit(monkeypatch):
    controls = _controls(6)

    class _All:
        def rank_ids(self, controls, constraints, goal, density, limit=None):
            return ["missing", *[control.id for control in reversed(controls)]]

    monkeypatch.setenv("WAYFINDER_MODEL", "asione")
    monkeypatch.setattr("wayfinder.model_backend.resolve_backend", lambda: _All())
    goal = Goal(text="shorter list")
    brief = model_rank(controls, [], goal, "brief", None)
    assert [control.id for control in brief] == ["c5", "c4", "c3"]
    limited = model_rank(controls, [], goal, "standard", 3)
    assert [control.id for control in limited] == ["c5", "c4", "c3"]
    assert len(model_rank(controls, [], goal, "full", 5)) == 5
    assert len(rank(controls, [], "brief", None)) == 3
    assert len(rank(controls, [], "standard", 3)) == 3


def test_model_summary_falls_back_on_timeout_and_parse_error(monkeypatch, harbor):
    snapshot, goal = harbor
    fallback = _interpreter_summary(snapshot, goal, "standard")
    calls = {"n": 0}

    def timeout(request, timeout=None):
        calls["n"] += 1
        assert timeout == 6
        raise TimeoutError("timed out")

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    assert model_summary(snapshot, goal, "standard") == fallback

    def bad_json(request, timeout=None):
        calls["n"] += 1
        return _ollama_body("this is not json")

    monkeypatch.setattr(urllib.request, "urlopen", bad_json)
    assert model_summary(snapshot, goal, "standard") == fallback

    def empty_summary(request, timeout=None):
        return _ollama_body(json.dumps({"summary": "  "}))

    monkeypatch.setattr(urllib.request, "urlopen", empty_summary)
    assert model_summary(snapshot, goal, "standard") == fallback
    assert calls["n"] >= 2


def test_model_summary_uses_backend_text_when_it_parses(monkeypatch, harbor):
    snapshot, goal = harbor

    def ok(request, timeout=None):
        return _ollama_body(json.dumps({"summary": "  A clearer page summary.  "}))

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    monkeypatch.setattr(urllib.request, "urlopen", ok)
    assert model_summary(snapshot, goal, "standard") == "A clearer page summary."


def test_flag_off_does_not_call_the_model_and_keeps_rank_order(monkeypatch):
    called = False

    def explode(request, timeout=None):
        nonlocal called
        called = True
        raise AssertionError("model backend was called while WAYFINDER_MODEL is off")

    monkeypatch.delenv("WAYFINDER_MODEL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    view = create_session()
    snapshot = build_snapshot(PageState())
    expected = [control.id for control in rank(snapshot.controls, [], view["density"], view["memory"]["preferences"]["branch_limit"])]
    assert [branch["id"] for branch in view["branches"]] == expected
    assert view["wir"]["page"]["summary"] == snapshot.summary
    assert view["page"]["summary"] == snapshot.summary
    assert view["wir"]["page"]["purpose"] == snapshot.purpose
    assert called is False


def test_orchestrator_applies_model_summary_and_order_only(monkeypatch):
    def respond(request, timeout=None):
        payload = json.loads(request.data.decode())
        user = payload["messages"][-1]["content"]
        facts = json.loads(user.split("\n", 1)[1])
        if "extracted_summary" in facts:
            content = json.dumps({"summary": "Model page summary."})
        else:
            ids = [item["id"] for item in reversed(facts["controls"])]
            content = json.dumps({"ids": ["invented-control", *ids]})
        return _ollama_body(content)

    monkeypatch.setenv("WAYFINDER_MODEL", "ollama")
    monkeypatch.setattr(urllib.request, "urlopen", respond)
    view = create_session()
    snapshot = build_snapshot(PageState())
    assert view["wir"]["page"]["summary"] == "Model page summary."
    assert view["page"]["summary"] == view["wir"]["page"]["summary"]
    assert view["page"]["summary"] == "Model page summary."
    assert "Stations shown:" not in view["page"]["summary"]
    assert view["wir"]["page"]["title"] == snapshot.title
    assert view["wir"]["page"]["purpose"] == snapshot.purpose
    assert view["wir"]["page"]["url"] == snapshot.url
    reversed_ids = [control.id for control in reversed(snapshot.controls)]
    assert [branch["id"] for branch in view["branches"]] == reversed_ids
    labels = {control.id: control.label for control in snapshot.controls}
    assert {branch["id"]: branch["label"] for branch in view["branches"]} == {
        branch_id: labels[branch_id] for branch_id in reversed_ids
    }
    assert "invented-control" not in {branch["id"] for branch in view["branches"]}

    reread = handle_turn(view["session_id"], action_id="explain_access")
    assert reread["verification"]["status"] == "SUCCESS"
    assert reread["wir"]["page"]["summary"] == "Model page summary."
    assert "invented-control" not in {branch["id"] for branch in reread["branches"]}


def _http_429(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(b""))


def test_asione_429_or_missing_key_falls_back_without_raising(monkeypatch, harbor):
    snapshot, goal = harbor
    fallback = _interpreter_summary(snapshot, goal, "standard")
    expected = [control.id for control in rank(snapshot.controls, goal.constraints, "standard", 5)]
    monkeypatch.setenv("WAYFINDER_MODEL", "asione")
    monkeypatch.delenv("WAYFINDER_ASIONE_KEY", raising=False)
    monkeypatch.delenv("WAYFINDER_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("WAYFINDER_OLLAMA_RANK_MODEL", raising=False)
    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: False)

    assert model_summary(snapshot, goal, "standard") == fallback
    assert [control.id for control in model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)] == expected

    seen: list[str] = []

    def respond(request, timeout=None):
        seen.append(request.full_url)
        assert timeout == 6
        if "11434" not in request.full_url:
            raise _http_429(request.full_url)
        payload = json.loads(request.data.decode())
        assert payload["model"] == "llama3.1:latest"
        user = payload["messages"][-1]["content"]
        facts = json.loads(user.split("\n", 1)[1])
        if "extracted_summary" in facts:
            return _ollama_body(json.dumps({"summary": "Ollama fallback summary."}))
        return _ollama_body(json.dumps({"ids": [facts["controls"][-1]["id"], "not-a-control"]}))

    monkeypatch.setattr("wayfinder.model_backend._ollama_reachable", lambda timeout=0.3: True)
    monkeypatch.setattr(urllib.request, "urlopen", respond)
    assert model_summary(snapshot, goal, "standard") == "Ollama fallback summary."
    ranked = model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)
    assert [control.id for control in ranked] == [snapshot.controls[-1].id]
    assert seen and all("11434" in url for url in seen)

    monkeypatch.setenv("WAYFINDER_MODEL", "auto")
    monkeypatch.setenv("WAYFINDER_ASIONE_KEY", "free-tier")
    seen.clear()
    assert model_summary(snapshot, goal, "standard") == "Ollama fallback summary."
    assert any("chat/completions" in url for url in seen)
    assert any("11434" in url for url in seen)

    def always_429(request, timeout=None):
        raise _http_429(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", always_429)
    assert model_summary(snapshot, goal, "standard") == fallback
    assert [control.id for control in model_rank(snapshot.controls, goal.constraints, goal, "standard", 5)] == expected


def test_ollama_live_smoke():
    if not _ollama_reachable():
        pytest.skip("Ollama is not running on localhost:11434")
    snapshot = build_snapshot(PageState())
    goal = Goal(text="Find a step-free stop")
    backend = OllamaBackend()
    text = backend.summary(snapshot, goal, "brief")
    assert isinstance(text, str) and text.strip()
    known = {control.id for control in snapshot.controls}
    ids = backend.rank_ids(snapshot.controls, ["step_free"], goal, "standard", 3)
    assert ids
    assert set(ids) <= known
    assert len(ids) <= 3
