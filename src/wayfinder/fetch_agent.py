"""Standalone Fetch.ai uAgent for the Wayfinder Reader capability.

Not imported by the Mac orchestrator or API. Install with ``pip install -e '.[fetch]'``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Literal

try:
    from uagents import Agent, Context, Model, Protocol
except ImportError as exc:  # pragma: no cover - optional extra
    raise ImportError(
        "wayfinder.fetch_agent requires the fetch optional extra: pip install -e '.[fetch]'"
    ) from exc

from wayfinder import capabilities
from wayfinder.browser.bridge import BrowserBridge, BrowserUnavailable
from wayfinder.browser.families import normalize_url
from wayfinder.demo_site import build_snapshot
from wayfinder.devices import DevicePlane
from wayfinder.interpreter import interpret
from wayfinder.models import A11yNode, Entity, Goal, PageState, Snapshot
from wayfinder.ranker import rank

READER = capabilities.get("reader")
PageTarget = Literal["faq", "search", "form", "unlabeled", "shop", "jobs", "fixture"]
ALLOWED_TARGETS = frozenset({"faq", "search", "form", "unlabeled", "shop", "jobs", "fixture"})
PROTOCOL_NAME = "WayfinderReader"
PROTOCOL_VERSION = "0.1.0"

_plane = DevicePlane()
_bridge: BrowserBridge | None = None


class ReaderPageError(RuntimeError):
    """The page could not be loaded for a read-only orientation."""


class ReaderRequest(Model):
    target: PageTarget = "faq"
    url: str | None = None


class ReaderBranch(Model):
    id: str
    label: str
    capability: str
    risk_class: str


class ReaderResponse(Model):
    capability_id: str = READER.id
    capability_name: str = READER.name
    trust: str = READER.trust
    target: str
    summary: str
    branch_labels: list[str]
    branches: list[ReaderBranch]
    evidence_ref: str = ""
    pipeline_tool: str = "interpret+rank"
    error: str | None = None


reader_proto = Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)


def _get_bridge() -> BrowserBridge:
    global _bridge
    if _bridge is None:
        _bridge = BrowserBridge()
    return _bridge


def _browser_snapshot(observation, controls) -> Snapshot:
    entities = [
        Entity(id=f"topic-{index}", type="topic", name=topic, trust="observed")
        for index, topic in enumerate(observation.topics[:8])
    ]
    tree = [A11yNode(role="heading", name=observation.h1)]
    tree.extend(
        A11yNode(role="heading", name=str(item.get("text") or item.get("summary") or ""))
        for item in observation.headings[:8]
    )
    return Snapshot(
        url=observation.url,
        title=observation.title,
        purpose=observation.purpose,
        summary=observation.summary,
        entities=entities,
        controls=controls,
        accessibility_tree=tree,
        untrusted_text=list(observation.untrusted),
        view="overview",
        step_free_only=False,
        alert="none",
        stations=[],
        routes=[],
    )


def _load_snapshot(target: PageTarget, url: str | None) -> tuple[Snapshot, list, str]:
    if target == "fixture" and not url:
        snapshot = build_snapshot(PageState())
        return snapshot, list(snapshot.controls), "snapshot:harbor"

    session_id = f"fetch-{uuid.uuid4().hex}"
    bridge = _get_bridge()
    try:
        if url:
            observation = bridge.open(session_id, "url", normalize_url(url))
        else:
            observation = bridge.open(session_id, target, None)
    except BrowserUnavailable as exc:
        raise ReaderPageError(
            f"Browser adapter unavailable ({exc}). Use target fixture for offline demo."
        ) from exc
    except ValueError as exc:
        raise ReaderPageError(f"Invalid URL ({exc}).") from exc

    controls = observation.controls
    return _browser_snapshot(observation, controls), controls, observation.evidence_ref


def run_reader_read(request: ReaderRequest) -> ReaderResponse:
    """Tool call into the real Reader pipeline: snapshot → interpret → rank."""
    turn_id = uuid.uuid4().hex
    trace_id = uuid.uuid4().hex
    try:
        snapshot, controls, evidence_ref = _load_snapshot(request.target, request.url)
    except ReaderPageError as exc:
        return ReaderResponse(
            target=request.target,
            summary="",
            branch_labels=[],
            branches=[],
            error=str(exc),
        )

    density = "standard"
    wir = interpret(
        snapshot,
        session_id="fetch-agent",
        turn_id=turn_id,
        trace_id=trace_id,
        goal=Goal(),
        channels=_plane.channels(),
        devices=_plane.directory(),
        history_summary="",
        density=density,
    )
    branches = rank(controls, [], density, limit=5)
    branch_models = [
        ReaderBranch(
            id=branch.id,
            label=branch.label,
            capability=branch.capability,
            risk_class=branch.risk_class,
        )
        for branch in branches
    ]
    return ReaderResponse(
        target=request.target,
        summary=str(wir.page.get("summary") or snapshot.summary),
        branch_labels=[branch.label for branch in branches],
        branches=branch_models,
        evidence_ref=evidence_ref,
    )


@reader_proto.on_message(model=ReaderRequest, replies={ReaderResponse})
async def handle_reader_request(ctx: Context, sender: str, msg: ReaderRequest) -> None:
    ctx.logger.info("Reader tool call: target=%s url=%s", msg.target, msg.url or "")
    response = await asyncio.to_thread(run_reader_read, msg)
    if response.error:
        ctx.logger.warning("Reader pipeline error: %s", response.error)
    else:
        ctx.logger.info(
            "Reader returned %d branches for %s",
            len(response.branch_labels),
            msg.target,
        )
    await ctx.send(sender, response)


def _agent_config() -> dict:
    port = int(os.environ.get("WAYFINDER_FETCH_PORT", "8010"))
    seed = os.environ.get("WAYFINDER_FETCH_SEED")
    register = os.environ.get("WAYFINDER_FETCH_REGISTER", "").strip().lower() in {"1", "true", "yes"}
    network = os.environ.get("WAYFINDER_FETCH_NETWORK", "testnet")
    if network not in {"mainnet", "testnet"}:
        network = "testnet"
    name = os.environ.get("WAYFINDER_FETCH_NAME", "wayfinder-reader")
    description = (
        "Read-only Wayfinder Reader agent. Calls interpret+rank on a page target and "
        "returns a semantic summary with grounded branch labels. Trust: read_only."
    )
    endpoint = [f"http://127.0.0.1:{port}/submit"]
    cfg: dict = {
        "name": name,
        "port": port,
        "endpoint": endpoint,
        "mailbox": register,
        "network": network,
        "description": description,
        "publish_agent_details": register,
        "readme_path": os.path.join(
            os.path.dirname(__file__), "..", "..", "docs", "sponsor-evidence", "fetch.md"
        ),
    }
    if seed:
        cfg["seed"] = seed
    if register:
        cfg["metadata"] = {
            "capability_registry_id": READER.id,
            "permissions": list(READER.permissions),
            "router_capabilities": list(READER.router_capabilities),
        }
    return cfg


def _agent_event_loop() -> asyncio.AbstractEventLoop:
    """uAgents Agent() calls get_event_loop(); Python 3.10+ main has none until set."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def build_agent() -> Agent:
    agent = Agent(**_agent_config(), loop=_agent_event_loop())
    agent.include(reader_proto)

    register = os.environ.get("WAYFINDER_FETCH_REGISTER", "").strip().lower() in {"1", "true", "yes"}

    @agent.on_event("startup")
    async def on_startup(ctx: Context) -> None:
        mode = "Agentverse mailbox" if register else "local endpoint only"
        ctx.logger.info(
            "%s (%s) address=%s mode=%s",
            READER.name,
            READER.id,
            agent.address,
            mode,
        )
        ctx.logger.info("Protocol %s@%s", PROTOCOL_NAME, PROTOCOL_VERSION)
        if os.environ.get("WAYFINDER_ASIONE_KEY", "").strip():
            ctx.logger.info("WAYFINDER_ASIONE_KEY set (optional ASI:One chat evidence only)")
        else:
            ctx.logger.info(
                "WAYFINDER_ASIONE_KEY unset — normal; agent runs without ASI:One"
            )

    return agent


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        target = sys.argv[2] if len(sys.argv) > 2 else "faq"
        if target not in ALLOWED_TARGETS:
            raise SystemExit(f"Unknown target: {target}")
        import json

        result = run_reader_read(ReaderRequest(target=target))  # type: ignore[arg-type]
        print(json.dumps(result.model_dump(), indent=2))
        return
    build_agent().run()


if __name__ == "__main__":
    main()
