"""Turn loop. The Mac owns WIR, policy, verification, and device dispatch."""

from __future__ import annotations

import time
import uuid

from wayfinder import capabilities, conversation, semantic
from wayfinder.browser.bridge import BrowserUnavailable
from wayfinder.browser.families import PageObservation, add_controls, entry_control, failure_observation, normalize_url
from wayfinder.contracts import ADR_ID, BOX3_ID, DEVKIT_ID, P4_ID
from wayfinder.demo_site import build_snapshot
from wayfinder.devices import DispatchResult
from wayfinder.executor import execute
from wayfinder.gateway import constraints_in, parse
from wayfinder.interpreter import interpret
from wayfinder.model_backend import model_enabled, model_rank, model_summary
from wayfinder.models import (
    A11yNode,
    AgentEnvelope,
    Control,
    DeviceMessage,
    Entity,
    Intent,
    Snapshot,
    TopologyError,
    parse_device_message,
)
from wayfinder.observability import export_event, sentry_turn, trace_event
from wayfinder.policy import decide
from wayfinder.ranker import rank
from wayfinder.renderers import (
    glance_payload,
    grade1_braille,
    narrate_result,
    orientation_speech,
    salient_outcome,
    still_on_page,
)
from wayfinder.router import UnknownCapability, route
from wayfinder.state import Session, get_runtime, runtime
from wayfinder.models import Verification
from wayfinder.verifier import verify, verify_browser

HELP = (
    "You can say what is here, what can I do, explain, compare, go back, continue, "
    "stop, less detail, more detail, or a number for one of the options."
)
HARDWARE_NOTE = (
    "Tactile output is simulated unless a DevKit USB acknowledgement says otherwise. "
    "The 130 mini DC brushless motor is not attached to this software, and no physical motor run is claimed."
)
RISK_LABELS = {
    "read": "Read only",
    "reversible_write": "Changes this page",
    "consequential": "Needs confirmation",
    "irreversible": "Blocked",
}
_SUPERSEDE = {"what_is_here", "explain", "compare", "select", "go_back"}
# Words about the conversation itself are never read as page constraints.
_NO_CONSTRAINT = {
    "forget_constraint", "where_was_i", "repeat", "explain_that", "wait", "just_do_it", "mute", "unmute",
    "set_renderer", "save_project", "continue_project", "set_preference", "reset_preferences", "who_acting",
    "read_all",
}
_DENSITY_DOWN = {"full": "standard", "standard": "brief", "brief": "brief"}
_DENSITY_UP = {"brief": "standard", "standard": "full", "full": "full"}


class SessionNotFound(Exception):
    """No Mac session matches the id."""


def create_session(target: str | None = None, url: str | None = None, continue_from: str | None = None) -> dict:
    allowed = {None, "", "fixture", "faq", "search", "form", "unsupported", "public", "url", "shop", "jobs", "unlabeled"}
    if url and target in {None, ""}:
        target = "url"
    if target not in allowed:
        raise TopologyError("unknown_page_target")
    if target == "url" or url:
        # Only a plain http(s) address reaches the browser. A page cannot choose this value.
        if target != "url":
            raise TopologyError("invalid_url")
        try:
            url = normalize_url(url)
        except ValueError as exc:
            raise TopologyError("invalid_url") from exc
    with runtime.lock:
        session = Session()
        conversation.apply_bank(session)
        previous = runtime.sessions.get(continue_from or "")
        if previous is not None:
            conversation.inherit(session, previous)
        runtime.sessions[session.id] = session
        runtime.active_session_id = session.id
        open_target(session, target, url)
        return _turn(session, "what is here", None, "text")


def open_target(session: Session, target: str | None, url: str | None = None) -> None:
    """Point a session at a page. The task state (goal, path, preferences) is not touched."""
    session.pending = None
    session.branches = []
    session.observation = None
    session.fallback_reason = ""
    session.fallback_announced = False
    if target and target != "fixture":
        session.browser_target = target
        try:
            runtime.browser.open(session.id, target, url)
            session.mode = "browser"
        except BrowserUnavailable as exc:
            session.mode = "fixture"
            session.browser_target = "fixture"
            session.fallback_reason = (
                f"The browser adapter is unavailable ({exc}). "
                "This is the labeled Harbor Line fixture, not the live browser."
            )
    else:
        session.mode = "fixture"
        session.browser_target = "fixture"


def current_title(session: Session) -> str:
    if session.mode == "browser" and session.observation is not None:
        return session.observation.h1 or session.observation.title
    return "Harbor Line"


def handle_turn(
    session_id: str,
    text: str | None = None,
    action_id: str | None = None,
    channel: str = "text",
) -> dict:
    with runtime.lock:
        session = _session(session_id)
        return _turn(session, text, action_id, channel)


def session_view(session_id: str) -> dict:
    with runtime.lock:
        return _view(_session(session_id))


def session_traces(session_id: str) -> list[dict]:
    with runtime.lock:
        return list(_session(session_id).traces)


def set_fault(session_id: str, name: str, enabled: bool) -> dict:
    allowed = {"selector_failure", "verification_mismatch", "device_timeout"}
    if name not in allowed:
        raise TopologyError("unknown_fault")
    with runtime.lock:
        session = _session(session_id)
        if enabled:
            session.faults.add(name)
        else:
            session.faults.discard(name)
        return _view(session)


def set_connection(board_id: str, status: str) -> dict[str, str]:
    with runtime.lock:
        board = runtime.plane.set_connection(board_id, status)
        return runtime.plane.entry(board.board_id)


def ping_device(board_id: str) -> dict:
    with runtime.lock:
        if board_id not in runtime.plane.boards:
            raise TopologyError("unknown_endpoint")
        result = runtime.plane.ping(board_id)
        board = runtime.plane.boards[board_id]
        if result.simulated and result.acknowledged:
            detail = "Simulator health acknowledgement. The physical motor has not spun."
        elif board.loopback and result.acknowledged:
            detail = "Loopback health acknowledgement. No physical board answered, and the motor has not spun."
        elif result.acknowledged:
            detail = "The board acknowledged health on USB. This was not a motor run."
        else:
            detail = "No health acknowledgement. The physical motor has not spun."
        return {
            "board_id": board_id,
            "status": result.status,
            "acknowledged": result.acknowledged,
            "simulated": result.simulated,
            "physical_servo_moved": False,
            "physical_motor_ran": False,
            "device_status": runtime.plane.entry(board_id)["status"],
            "angle_deg": board.last_angle,
            "detail": detail,
        }


def set_actuator(enabled: bool) -> dict[str, str]:
    with runtime.lock:
        runtime.plane.set_actuator(enabled)
        return runtime.plane.entry(DEVKIT_ID)


def ingest_message(session_id: str, data: dict[str, object]) -> dict:
    message = parse_device_message(data)
    with runtime.lock:
        return _ingest(_session(session_id), message)


def on_board_event(message: DeviceMessage) -> None:
    with runtime.lock:
        session_id = runtime.active_session_id
        session = runtime.sessions.get(session_id or "")
        if session is None:
            runtime.plane.dropped_events += 1
            return
        _ingest(session, message)


def _install_board_callback() -> None:
    get_runtime().plane.on_event = on_board_event


def _session(session_id: str) -> Session:
    try:
        return runtime.sessions[session_id]
    except KeyError as exc:
        raise SessionNotFound(session_id) from exc


def _turn(session: Session, text: str | None, action_id: str | None, channel: str) -> dict:
    turn_id = uuid.uuid4().hex
    trace_id = uuid.uuid4().hex
    with sentry_turn(trace_id=trace_id, session_id=session.id, turn_id=turn_id):
        return _turn_body(session, text, action_id, channel, turn_id, trace_id)


def _turn_body(
    session: Session,
    text: str | None,
    action_id: str | None,
    channel: str,
    turn_id: str,
    trace_id: str,
) -> dict:
    events: list[dict] = []
    session.last_verification = None
    session.last_decision = None
    conversation.begin_turn(session)
    if action_id:
        intent = Intent(type="select", text=action_id, action_id=action_id, channel=channel)
    else:
        intent = parse(text or "", session.branches, channel=channel, pending=session.pending is not None)
        if text and intent.type not in _NO_CONSTRAINT:
            for constraint in constraints_in(text):
                if constraint not in session.goal.constraints:
                    session.goal.constraints.append(constraint)
                    session.goal.text = text.strip()
                    conversation.note(session, "constraint", f"Added the {constraint.replace('_', ' ')} constraint")
    _emit(session, events, trace_id, turn_id, "gateway", "parse_intent", "ok", intent=intent.type, channel=channel)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "orchestrator",
        "retrieve_context",
        "ok",
        evidence_ref=(
            session.observation.evidence_ref
            if session.mode == "browser" and session.observation is not None
            else (f"wir:{session.id}" if session.wir else "snapshot:harbor")
        ),
    )

    if session.pending and intent.type == "confirm":
        control = session.pending
        session.pending = None
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "orchestrator",
            "confirm",
            "ok",
            action_id=control.id,
            confirmation_outcome="confirmed",
            risk_class=control.risk_class,
        )
        conversation.note(session, "confirm", f"You confirmed: {control.label}")
        view = _commit(session, control, True, turn_id, trace_id, events, channel)
        if conversation.after_confirm(session, control):
            view = _view(session)
        return view

    if session.pending and intent.type in {"deny", "stop"}:
        control = session.pending
        outcome = "declined" if intent.type == "deny" else "stopped"
        session.pending = None
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "orchestrator",
            "cancel_pending",
            "ok",
            confirmation_outcome=outcome,
            risk_class=control.risk_class,
        )
        _remember_decision(
            session,
            events,
            trace_id,
            turn_id,
            chosen_action=control.id,
            chosen_label=control.label,
            evidence=f"action:{control.id}",
            alternative_branches=[branch.id for branch in session.branches if branch.id != control.id],
            risk_class=control.risk_class,
            policy_outcome=outcome,
            expected_postcondition=dict(control.expected_postcondition),
            observed_postcondition={},
            confidence="unresolved",
            confirmation_outcome=outcome,
        )
        _publish(session, events, trace_id, turn_id, "cleared", "Cleared", detach=True)
        conversation.note(session, "decline", f"You {'declined' if intent.type == 'deny' else 'stopped'}: {control.label}. Nothing was submitted")
        conversation.pause_goal(session)
        speech = (
            "Stopped. Nothing was submitted. The page is unchanged."
            if intent.type == "stop"
            else "Not submitted. The page is unchanged."
        )
        return _finish(session, speech, turn_id, trace_id, events)

    if session.pending and intent.type in _SUPERSEDE:
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "orchestrator",
            "supersede_pending",
            "ok",
            replaced=session.pending.id,
        )
        session.pending = None

    handled = conversation.handle(session, intent, turn_id, trace_id, events, channel)
    if handled is not None:
        return handled
    if intent.type == "stop":
        _emit(session, events, trace_id, turn_id, "orchestrator", "stop", "ok")
        _publish(session, events, trace_id, turn_id, "cleared", "Cleared", detach=True)
        conversation.pause_goal(session)
        conversation.note(session, "stop", "You stopped")
        conversation.select(session, events, trace_id, turn_id, "orchestrator", "stop: goal and steps kept")
        return _finish(
            session,
            "Stopped. Your place, your goal, and the steps already taken are kept.",
            turn_id, trace_id, events, meta=True,
        )
    if intent.type == "confirm":
        return _finish(session, "Nothing is waiting for confirmation.", turn_id, trace_id, events)
    if intent.type == "deny":
        return _finish(session, "Nothing is waiting to cancel.", turn_id, trace_id, events)
    if intent.type == "less_detail":
        before = session.density
        session.density = _DENSITY_DOWN[session.density]
        conversation.persist_density(session)
        note = "Already at the shortest detail." if before == session.density else f"Showing less detail. Detail is now {session.density}."
        return _reorient(session, turn_id, trace_id, events, note)
    if intent.type == "more_detail":
        before = session.density
        session.density = _DENSITY_UP[session.density]
        conversation.persist_density(session)
        note = "Already at the fullest detail." if before == session.density else f"Showing more detail. Detail is now {session.density}."
        return _reorient(session, turn_id, trace_id, events, note)
    if intent.type == "go_back":
        if session.mode == "browser":
            before = session.observation.url if session.observation is not None else ""
            try:
                moved = runtime.browser.back(session.id)
            except Exception:
                moved = False
            if not moved:
                _emit(session, events, trace_id, turn_id, "orchestrator", "go_back", "rejected", evidence_ref=f"browser:{before}")
                return _finish(session, "You are already at the start of this page.", turn_id, trace_id, events)
            _emit(session, events, trace_id, turn_id, "orchestrator", "go_back", "ok", evidence_ref=f"browser:{before}")
            conversation.note(session, "back", "You went back")
            reply = _reorient(session, turn_id, trace_id, events, "Back on the previous page.")
            after = session.observation.url if session.observation is not None else ""
            changed = bool(after) and after != before
            session.last_verification = Verification(
                status="SUCCESS" if changed else "FAILURE",
                expected={"url_changed": True},
                observed={"url": after},
                detail=f"Fresh browser read: {after}" if changed else "The URL did not change.",
            )
            _emit(session, events, trace_id, turn_id, "verifier", "check_postcondition", session.last_verification.status, expected={"url_changed": True}, observed={"url": after}, evidence_ref=session.observation.evidence_ref if session.observation else "")
            return _view(session)
        if not session.history:
            return _finish(session, "You are already at the start of this task.", turn_id, trace_id, events)
        session.page = session.history.pop()
        _emit(session, events, trace_id, turn_id, "orchestrator", "go_back", "ok")
        return _reorient(session, turn_id, trace_id, events, "Back on the previous page.")
    if intent.type == "continue":
        if session.pending:
            speech = f"{session.pending.preview} This still needs confirmation. Say yes or no."
            return _finish(session, speech, turn_id, trace_id, events)
        if session.goal_plan and session.goal_plan.get("status") in {"paused", "planned", "need_input", "stuck"}:
            conversation.select(session, events, trace_id, turn_id, "goal_planner", "carry on with the goal")
            return conversation.advance_goal(session, turn_id, trace_id, events, channel)
        return _reorient(session, turn_id, trace_id, events, "Continuing from the current page.")
    if intent.type == "what_is_here":
        return _reorient(session, turn_id, trace_id, events, "")
    if intent.type == "explain":
        return _commit(session, _require(session, "reader.explain"), False, turn_id, trace_id, events, channel)
    if intent.type == "compare":
        return _commit(session, _require(session, "reader.compare"), False, turn_id, trace_id, events, channel)
    if intent.type == "enter_text":
        return _enter_text(session, intent, turn_id, trace_id, events, channel)
    if intent.type == "select":
        control = _resolve(intent, session.branches)
        if control is None:
            _emit(session, events, trace_id, turn_id, "orchestrator", "invalid_branch", "rejected")
            _publish(session, events, trace_id, turn_id, "failure", "Option rejected")
            speech = "That is not one of the current options. The page is unchanged."
            return _finish(session, speech, turn_id, trace_id, events)
        return _commit(session, control, False, turn_id, trace_id, events, channel)
    if intent.type == "ambiguous":
        count = len(session.branches)
        speech = f"More than one option matches. Say a number from 1 to {count}."
        if session.pending:
            speech += " A confirmation is still waiting."
        return _finish(session, speech, turn_id, trace_id, events)

    speech = HELP
    if session.pending:
        speech = f"I didn't take that as yes or no. {session.pending.preview} Say yes or no."
    _emit(session, events, trace_id, turn_id, "gateway", "unknown_intent", "rejected")
    return _finish(session, speech, turn_id, trace_id, events)


def _apply_model(wir, snapshot, controls, session, branches):
    """Replace summary text and branch order when a model backend is selected.

    WAYFINDER_MODEL=off returns the deterministic branches and leaves the summary alone.
    """
    if not model_enabled():
        return branches
    wir.page["summary"] = model_summary(snapshot, session.goal, session.density)
    return model_rank(
        controls,
        session.goal.constraints,
        session.goal,
        session.density,
        session.branch_limit,
    )


def _reorient(session: Session, turn_id: str, trace_id: str, events: list[dict], prefix: str) -> dict:
    if session.mode == "browser":
        return _reorient_browser(session, turn_id, trace_id, events, prefix)
    if session.fallback_reason and not session.fallback_announced:
        session.fallback_announced = True
        prefix = f"{session.fallback_reason} {prefix}".strip()
    started = time.perf_counter()
    snapshot = build_snapshot(session.page)
    wir = interpret(
        snapshot,
        session_id=session.id,
        turn_id=turn_id,
        trace_id=trace_id,
        goal=session.goal,
        channels=runtime.plane.channels(),
        devices=runtime.plane.directory(),
        history_summary=" ".join(session.memory_lines)[-500:],
        density=session.density,
    )
    branches = rank(snapshot.controls, session.goal.constraints, session.density, session.branch_limit)
    branches = _apply_model(wir, snapshot, snapshot.controls, session, branches)
    wir.actions = branches
    session.wir = wir
    session.branches = branches
    _emit(session, events, trace_id, turn_id, "interpreter", "build_wir", "ok", started=started)
    _emit(session, events, trace_id, turn_id, "ranker", "rank_branches", "ok", count=len(branches))
    summary_control = next(control for control in snapshot.controls if control.capability == "reader.explain")
    # Routing a read shows the registry is in the orientation path. Summarize is the capability.
    chosen = route(_reader_stand_in(summary_control))
    conversation.select(session, events, trace_id, turn_id, "reader", "read the page")
    conversation.select(session, events, trace_id, turn_id, "branch_planner", f"{len(branches)} grounded choices", headline=False)
    conversation.note_page(session, "Harbor Line fixture", "fixture:harbor")
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "router",
        "select_capability",
        "ok",
        capability="reader.summarize",
        agent=chosen.agent,
        permissions=["read"],
    )
    decision = decide(summary_control, confirmed=False)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "policy",
        "decide",
        decision.decision,
        risk_class="read",
        permissions=["read"],
    )
    _commit_wir(session, events, trace_id, turn_id)
    _remember_decision(
        session,
        events,
        trace_id,
        turn_id,
        chosen_action="reader.summarize",
        chosen_label="Read the page",
        evidence="snapshot:harbor",
        alternative_branches=[branch.id for branch in branches],
        risk_class="read",
        policy_outcome=decision.decision,
        expected_postcondition={"oriented": True},
        observed_postcondition={"oriented": True},
        confidence="certain",
        confirmation_outcome="not_required",
    )
    speech = orientation_speech(wir.page["summary"], branches, session.density)
    if prefix:
        speech = f"{prefix} {speech}"
    _publish(session, events, trace_id, turn_id, "branch_ready", "Options ready")
    return _finish(session, speech, turn_id, trace_id, events)


def _reader_stand_in(control: Control) -> Control:
    """Route the orientation read through the registry without a second capability id."""
    return control.model_copy(update={"capability": "reader.summarize", "risk_class": "read"})


def _commit(
    session: Session,
    control: Control,
    confirmed: bool,
    turn_id: str,
    trace_id: str,
    events: list[dict],
    channel: str,
) -> dict:
    if session.mode == "browser":
        return _commit_browser(session, control, confirmed, turn_id, trace_id, events, channel)
    current = {item.id: item for item in build_snapshot(session.page).controls}
    if control.capability.startswith("browser.") and control.id not in current:
        _emit(session, events, trace_id, turn_id, "orchestrator", "invalid_branch", "rejected")
        _publish(session, events, trace_id, turn_id, "failure", "Option rejected")
        return _finish(
            session,
            "That is not one of the current options. The page is unchanged.",
            turn_id,
            trace_id,
            events,
        )
    if control.id in current:
        control = current[control.id]
    try:
        chosen = route(control)
    except UnknownCapability:
        _emit(session, events, trace_id, turn_id, "router", "select_capability", "rejected", capability=control.capability)
        return _finish(session, "That capability is not in the registry.", turn_id, trace_id, events)
    decision = decide(control, confirmed=confirmed)
    conversation.select_for_control(session, events, trace_id, turn_id, control, chosen.agent, "harbor")
    conversation.note_policy(session, events, trace_id, turn_id, decision.decision)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "router",
        "select_capability",
        "ok",
        capability=chosen.capability,
        agent=chosen.agent,
        permissions=chosen.permissions,
    )
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "policy",
        "decide",
        decision.decision,
        risk_class=decision.risk_class,
        permissions=decision.permissions,
        reason=decision.reason,
    )
    envelope = AgentEnvelope(
        trace_id=trace_id,
        request_id=uuid.uuid4().hex,
        sender="orchestrator",
        recipient=chosen.agent,
        task_type=control.capability,
        input_refs=[f"wir:{session.id}:{turn_id}", f"action:{control.id}"],
        required_capability=control.capability,
        permissions=chosen.permissions,
        risk_class=control.risk_class,
        expected_output_schema="Verification@0.1",
        deadline_ms=10000,
        metadata={"user_confirmed": confirmed, "channel": channel},
    )
    _emit(session, events, trace_id, turn_id, "orchestrator", "agent_envelope", "ok", envelope=envelope.model_dump(mode="json"))
    if decision.decision == "DENY":
        _remember_decision(
            session,
            events,
            trace_id,
            turn_id,
            chosen_action=control.id,
            chosen_label=control.label,
            evidence=f"action:{control.id}",
            alternative_branches=[branch.id for branch in session.branches if branch.id != control.id],
            risk_class=control.risk_class,
            policy_outcome="DENY",
            expected_postcondition=dict(control.expected_postcondition),
            observed_postcondition={},
            confidence="none",
            confirmation_outcome="denied",
        )
        _publish(session, events, trace_id, turn_id, "failure", "Denied")
        return _finish(session, f"Denied. {decision.reason}", turn_id, trace_id, events)
    if decision.decision == "REQUIRE_CONFIRMATION":
        session.pending = control
        conversation.note(session, "confirm", f"Asked you to confirm: {control.label}")
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "policy",
            "confirmation_wait",
            "pending",
            risk_class=control.risk_class,
            confirmation_outcome="waiting",
            evidence_ref=f"action:{control.id}",
        )
        _remember_decision(
            session,
            events,
            trace_id,
            turn_id,
            chosen_action=control.id,
            chosen_label=control.label,
            evidence=f"action:{control.id}",
            alternative_branches=[branch.id for branch in session.branches if branch.id != control.id],
            risk_class=control.risk_class,
            policy_outcome="REQUIRE_CONFIRMATION",
            expected_postcondition=dict(control.expected_postcondition),
            observed_postcondition={},
            confidence="unresolved",
            confirmation_outcome="waiting",
        )
        _publish(session, events, trace_id, turn_id, "needs_confirmation", "Waiting for confirmation")
        speech = f"{control.preview} This needs confirmation. Say yes or no."
        return _finish(session, speech, turn_id, trace_id, events)

    started = time.perf_counter()
    new_page, result = execute(session.page, control, session.faults)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "executor",
        "apply",
        result.status,
        started=started,
        error_class=result.error_class,
        changed=result.changed,
    )
    if result.changed:
        session.history.append(session.page.model_copy(deep=True))
        session.history = session.history[-20:]
        session.page = new_page
    verification = verify(control, session.page)
    session.last_verification = verification
    conversation.note_verifier(session, events, trace_id, turn_id, verification.status)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "verifier",
        "check_postcondition",
        verification.status,
        expected=verification.expected,
        observed=verification.observed,
    )
    offered = [branch.id for branch in session.branches if branch.id != control.id]
    snapshot = build_snapshot(session.page)
    wir = interpret(
        snapshot,
        session_id=session.id,
        turn_id=turn_id,
        trace_id=trace_id,
        goal=session.goal,
        channels=runtime.plane.channels(),
        devices=runtime.plane.directory(),
        history_summary=" ".join(session.memory_lines)[-500:],
        density=session.density,
    )
    branches = rank(snapshot.controls, session.goal.constraints, session.density, session.branch_limit)
    branches = _apply_model(wir, snapshot, snapshot.controls, session, branches)
    wir.actions = branches
    session.wir = wir
    session.branches = branches
    _emit(session, events, trace_id, turn_id, "interpreter", "reread_wir", "ok")
    _commit_wir(session, events, trace_id, turn_id)
    observed = dict(verification.observed)
    _remember_decision(
        session,
        events,
        trace_id,
        turn_id,
        chosen_action=control.id,
        chosen_label=control.label,
        evidence=f"action:{control.id}",
        alternative_branches=offered,
        risk_class=control.risk_class,
        policy_outcome=decision.decision,
        expected_postcondition=dict(control.expected_postcondition),
        observed_postcondition=observed,
        confidence=_confidence(verification.status, dict(control.expected_postcondition), observed),
        confirmation_outcome="confirmed" if confirmed else "not_required",
    )
    if verification.status == "SUCCESS":
        runtime.remember(session, f"Chose {control.label}.")
        conversation.note(session, "read" if control.informational else "choice", f"{'Read' if control.informational else 'Chose'}: {control.label}.")
        token = "action_verified" if result.changed else "orient"
        label = "Verified state change" if result.changed else "Read verified"
        speech = narrate_result(salient_outcome(control, verification, snapshot.summary), branches, session.density)
    else:
        token = "failure"
        label = "Verification failed"
        if result.error_class == "selector_not_found":
            speech = narrate_result("That control is not on the page. Nothing changed.", branches, session.density)
        else:
            speech = narrate_result(still_on_page(snapshot.summary), branches, session.density)
    _publish(session, events, trace_id, turn_id, token, label)
    return _finish(session, speech, turn_id, trace_id, events)


def _require(session: Session, capability: str) -> Control:
    controls = session.branches if session.mode == "browser" else build_snapshot(session.page).controls
    for control in controls:
        if control.capability == capability:
            return control
    if session.mode == "browser":
        fresh = _observe(session)
        for control in fresh.controls:
            if control.capability == capability:
                return control
    raise TopologyError("unknown_capability")


def _resolve(intent: Intent, branches: list[Control]) -> Control | None:
    if intent.action_id:
        return next((branch for branch in branches if branch.id == intent.action_id), None)
    if intent.selection is None:
        return None
    if 1 <= intent.selection <= len(branches):
        return branches[intent.selection - 1]
    return None


def _publish(
    session: Session,
    events: list[dict],
    trace_id: str,
    turn_id: str,
    token: str,
    label: str,
    detach: bool = False,
) -> None:
    from wayfinder.renderers import tactile_payload

    payload = tactile_payload(token, label, detach=detach)
    plane = runtime.plane
    board = plane.boards[DEVKIT_ID]
    forced_timeout = "device_timeout" in session.faults and not detach
    sequence = None
    if forced_timeout or not board.connected or (not board.actuator_enabled and not detach):
        status = "timeout" if forced_timeout else ("disconnected" if not board.connected else "lockout")
        result = DispatchResult(
            status=status,
            acknowledged=False,
            simulated=board.simulated,
            fallback="software",
            latency_ms=0,
            device_angle=board.last_angle,
        )
    else:
        message_type = "safe_disable" if detach else "tactile.render"
        body = {"state_token": "cleared", "detach": True} if detach else payload
        message = plane.command(DEVKIT_ID, message_type, body, trace_id=trace_id)
        sequence = message.sequence
        result = plane.dispatch(message)
    session.tactile = {
        "state_token": token,
        "actuator": str(payload.get("actuator") or "130_mini_dc_brushless"),
        "duty_percent": int(payload.get("duty_percent") or 0),
        "duration_ms": int(payload.get("duration_ms") or 0),
        "direction": str(payload.get("direction") or "stop"),
        "angle_deg": int(payload["angle_deg"]),
        "pulse_us": int(payload["pulse_us"]),
        "label": label,
        "transport": "simulator" if board.simulated else board.link_kind,
        "acknowledged": result.acknowledged,
        "simulated": bool(board.simulated and board.connected),
        "loopback": board.loopback,
        "fallback": result.fallback,
        "device_status": board.status if result.acknowledged else result.status,
        "device_angle": board.last_angle,
    }
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "renderer",
        "software_tactile",
        "ok",
        state_token=token,
        angle_deg=payload["angle_deg"],
    )
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "device",
        "devkit_tactile",
        result.status,
        latency_ms=result.latency_ms,
        acknowledged=result.acknowledged,
        fallback=result.fallback,
        simulated=result.simulated,
        device_id=DEVKIT_ID,
        sequence=sequence,
        transport_status=result.status,
    )


def _finish(session: Session, speech: str, turn_id: str, trace_id: str, events: list[dict], *, meta: bool = False) -> dict:
    _emit(session, events, trace_id, turn_id, "renderer", "web_render", "ok")
    if not meta:
        session.last_substantive_speech = speech
    session.last_speech = speech
    session.last_events = events
    session.last_turn_id = turn_id
    session.last_trace_id = trace_id
    _push_box3(session, speech, trace_id, turn_id, events)
    return _view(session)


def _push_box3(session: Session, speech: str, trace_id: str, turn_id: str, events: list[dict]) -> None:
    from wayfinder.renderers import display_payload, speech_payload

    board = runtime.plane.boards[BOX3_ID]
    if not board.connected:
        session.box3_display = None
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "device",
            "box3_display",
            "skipped",
            fallback="software",
            device_id=BOX3_ID,
            transport_status="skipped",
        )
        return
    payload = display_payload(
        title=session.observation.h1 if session.mode == "browser" and session.observation is not None else "Harbor Line",
        body=speech,
        branches=session.branches,
        confirmation=session.pending is not None,
    )
    spoken = speech_payload(speech)
    display = runtime.plane.command(BOX3_ID, "display.render", payload, trace_id=trace_id)
    speech_message = runtime.plane.command(BOX3_ID, "speech.render", spoken, trace_id=trace_id)
    display_result = runtime.plane.dispatch(display)
    runtime.plane.dispatch(speech_message)
    session.box3_display = payload
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "device",
        "box3_display",
        display_result.status,
        fallback=display_result.fallback,
        simulated=display_result.simulated,
        device_id=BOX3_ID,
        sequence=display.sequence,
        transport_status=display_result.status,
    )


def _ingest(session: Session, message: DeviceMessage) -> dict:
    board = runtime.plane.boards.get(message.source)
    if board is None or not board.connected:
        raise TopologyError("device_disconnected")
    if message.destination != "mac_orchestrator":
        raise TopologyError("mac_must_be_endpoint")
    if message.type == "speech.event":
        return _turn(session, str(message.payload["transcript"]), None, "speech")
    if message.type == "touch.event":
        from wayfinder import tactile_loop

        payload = message.payload
        if "demo_input" in payload and message.source != DEVKIT_ID:
            raise TopologyError("invalid_payload")
        if message.source == DEVKIT_ID:
            # DevKit demo buttons and analog input share the keyboard command path.
            origin = tactile_loop.board_origin(board)
            if "demo_input" in payload:
                return tactile_loop.apply_input(session, str(payload["demo_input"]), payload.get("level"), origin)  # type: ignore[arg-type]
            if payload.get("control") in {"confirm", "stop"}:
                return tactile_loop.apply_input(session, str(payload["control"]), None, origin)
        if "selection" in message.payload:
            return _turn(session, str(message.payload["selection"]), None, "touch")
        word = {"confirm": "yes", "deny": "no", "stop": "stop"}[str(message.payload["control"])]
        return _turn(session, word, None, "touch")
    if message.type == "vision.event":
        return _vision(session, message)
    if message.type in {"ack", "health", "error"}:
        return _view(session)
    raise TopologyError("invalid_payload")


def _vision(session: Session, message: DeviceMessage) -> dict:
    turn_id = uuid.uuid4().hex
    trace_id = message.trace_id or uuid.uuid4().hex
    events: list[dict] = []
    event_name = str(message.payload.get("event"))
    summary = message.payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        note = f"Untrusted camera note: {summary.strip()}"
    elif event_name == "user_present":
        note = "Camera event: a person is present. No page action was taken."
    else:
        note = "Camera event recorded. No page action was taken."
    session.evidence.append(note[:240])
    session.evidence = session.evidence[-10:]
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "device",
        "p4_vision",
        "ok",
        event=event_name,
        evidence=note[:240],
    )
    speech = "A camera event was recorded as untrusted evidence. No page action was taken."
    session.last_events = events
    session.last_speech = speech
    session.last_turn_id = turn_id
    session.last_trace_id = trace_id
    return _view(session)


def _confidence(status: str, expected: dict, observed: dict) -> str:
    if status == "SUCCESS" and expected == observed:
        return "certain"
    if status == "PARTIAL":
        return "partial"
    if status == "FAILURE":
        return "none"
    return "unresolved"


def _commit_wir(session: Session, events: list[dict], trace_id: str, turn_id: str) -> None:
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "state",
        "commit_wir",
        "ok",
        evidence_ref=f"wir:{session.id}:{turn_id}",
    )


def _remember_decision(
    session: Session,
    events: list[dict],
    trace_id: str,
    turn_id: str,
    *,
    chosen_action: str,
    chosen_label: str,
    evidence: str,
    alternative_branches: list[str],
    risk_class: str,
    policy_outcome: str,
    expected_postcondition: dict,
    observed_postcondition: dict,
    confidence: str,
    confirmation_outcome: str,
) -> None:
    record = {
        "chosen_action": chosen_action,
        "chosen_label": chosen_label,
        "evidence": evidence,
        "alternative_branches": alternative_branches,
        "risk_class": risk_class,
        "policy_outcome": policy_outcome,
        "expected_postcondition": expected_postcondition,
        "observed_postcondition": observed_postcondition,
        "confidence": confidence,
    }
    session.last_decision = record
    session.last_explained = {
        **record,
        "verification": session.last_verification.status if session.last_verification else "not checked yet",
    }
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "audit",
        "decision_record",
        "ok",
        evidence_ref=evidence,
        confirmation_outcome=confirmation_outcome,
        **record,
    )


def _emit(
    session: Session,
    events: list[dict],
    trace_id: str,
    turn_id: str,
    source: str,
    action: str,
    status: str,
    started: float | None = None,
    **detail: object,
) -> None:
    if "started" in detail and isinstance(detail["started"], float):
        started = float(detail.pop("started"))
    item = trace_event(
        trace_id=trace_id,
        session_id=session.id,
        turn_id=turn_id,
        source=source,
        action=action,
        status=status,
        started=started if started is not None else time.perf_counter(),
        detail=detail,
    )
    session.traces.append(item)
    events.append(item)
    export_event(item)
    if len(session.traces) > 400:
        del session.traces[:100]


def _tactile_demo(session: Session) -> dict:
    from wayfinder import tactile_loop

    return tactile_loop.demo_view(session)


def _braille_view(speech: str) -> dict:
    cells = grade1_braille(speech)
    shown = cells[:480]
    # One entry per run of non-blank cells, so the plate can name the word it just formed.
    words = [token for token in speech.split(" ") if grade1_braille(token)]
    return {
        "grade": "1",
        "cells": shown,
        "words": words,
        "truncated": len(cells) > len(shown),
        "note": "Software Grade 1 projection of the live speech. Not a motor command and not a physical Braille device.",
    }


def _page_summary_for_view(session: Session, deterministic: str) -> str:
    """UI page summary: deterministic snapshot text unless a model backend rewrote WIR."""
    if model_enabled() and session.wir is not None:
        return str(session.wir.page.get("summary") or deterministic)
    return deterministic


def _page_view(session: Session, snapshot: Snapshot) -> dict:
    session_fallback = session.fallback_reason
    if session.mode == "browser" and session.observation is not None:
        observation = session.observation
        return {
            "url": observation.url,
            "title": observation.title,
            "purpose": observation.purpose,
            "summary": _page_summary_for_view(session, observation.summary),
            "view": observation.family,
            "step_free_only": False,
            "alert": "none",
            "stations": [],
            "routes": [],
            "untrusted_text": list(observation.untrusted),
            "kind": "browser",
            "family": observation.family,
            "supported": observation.supported,
            "html": observation.html,
            "evidence": observation.evidence(),
            "evidence_ref": observation.evidence_ref,
            "read_version": observation.version,
            "answer": observation.answer,
            "status": observation.status,
            "fallback_note": observation.error
            or "This page was read through the browser adapter (Playwright, headless Chromium).",
        }
    return {
        "url": snapshot.url,
        "title": snapshot.title,
        "purpose": snapshot.purpose,
        "summary": _page_summary_for_view(session, snapshot.summary),
        "view": snapshot.view,
        "step_free_only": snapshot.step_free_only,
        "alert": snapshot.alert,
        "stations": snapshot.stations,
        "routes": snapshot.routes,
        "untrusted_text": snapshot.untrusted_text,
        "kind": "fixture",
        "family": "harbor",
        "supported": True,
        "html": "",
        "evidence": [],
        "evidence_ref": "snapshot:harbor",
        "read_version": 0,
        "answer": "",
        "status": "",
        "fallback_note": (session_fallback + " " if session_fallback else "")
        + "Harbor Line fixture. This is the labeled offline fallback and regression fixture, not the live browser.",
    }


def _browser_snapshot(observation, controls: list[Control]) -> Snapshot:
    entities = [
        Entity(id=f"topic-{index}", type="topic", name=topic, trust="observed")
        for index, topic in enumerate(observation.topics[:8])
    ]
    tree = [A11yNode(role="heading", name=observation.h1)]
    tree.extend(A11yNode(role="heading", name=str(item.get("text") or item.get("summary") or "")) for item in observation.headings[:8])
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


def _observe(session: Session) -> PageObservation:
    """Fresh browser read. A crashed or missing page becomes an honest failure, not a guess."""
    try:
        return _prepare(session, runtime.browser.observe(session.id))
    except Exception as exc:  # noqa: BLE001
        return failure_observation("about:blank", f"The browser page could not be read ({type(exc).__name__}).")


def _prepare(session: Session, observation: PageObservation) -> PageObservation:
    """Apply this session's capability choices to a fresh read. The page itself is untouched."""
    off = session.capabilities_off
    observation.specialists_off = frozenset(item for item in off if item in capabilities.FAMILY_TO_ID.values())
    if "repair_agent" in off:
        for group in (observation.links, observation.buttons, observation.fields):
            for item in group:
                if item.get("inferred"):
                    item["name"] = ""
                    item["inferred"] = False
        observation.links = [item for item in observation.links if item.get("name")]
    return observation


def _enter_text(
    session: Session,
    intent: Intent,
    turn_id: str,
    trace_id: str,
    events: list[dict],
    channel: str,
) -> dict:
    if session.mode != "browser":
        return _finish(
            session,
            "Typing into a field needs a live browser page. This is the Harbor Line fixture, so nothing was typed.",
            turn_id,
            trace_id,
            events,
        )
    fresh = _observe(session)
    control = entry_control(fresh, intent.field or "", intent.value or "")
    if control is None:
        _emit(session, events, trace_id, turn_id, "orchestrator", "enter_text", "rejected", evidence_ref=fresh.evidence_ref)
        _publish(session, events, trace_id, turn_id, "failure", "No matching field")
        session.observation = fresh
        return _finish(
            session,
            f"I could not find a field called {intent.field} on this page. The page is unchanged.",
            turn_id,
            trace_id,
            events,
        )
    return _commit(session, control, False, turn_id, trace_id, events, channel)


def _reorient_browser(session: Session, turn_id: str, trace_id: str, events: list[dict], prefix: str) -> dict:
    started = time.perf_counter()
    observation = _observe(session)
    session.observation = observation
    controls = observation.controls
    snapshot = _browser_snapshot(observation, controls)
    wir = interpret(
        snapshot,
        session_id=session.id,
        turn_id=turn_id,
        trace_id=trace_id,
        goal=session.goal,
        channels=runtime.plane.channels(),
        devices=runtime.plane.directory(),
        history_summary=" ".join(session.memory_lines)[-500:],
        density=session.density,
    )
    branches = rank(controls, session.goal.constraints, session.density, session.branch_limit)
    branches = _apply_model(wir, snapshot, controls, session, branches)
    wir.actions = branches
    session.wir = wir
    session.branches = branches
    evidence = observation.evidence_ref
    _emit(session, events, trace_id, turn_id, "interpreter", "build_wir", "ok", started=started, evidence_ref=evidence)
    _emit(session, events, trace_id, turn_id, "ranker", "rank_branches", "ok", count=len(branches), family=observation.family)
    summary_control = next(control for control in controls if control.capability == "reader.explain")
    chosen = route(_reader_stand_in(summary_control))
    conversation.select(session, events, trace_id, turn_id, "reader", f"read {observation.h1}"[:90])
    conversation.select(session, events, trace_id, turn_id, "branch_planner", f"{len(branches)} grounded choices", headline=False)
    conversation.note_page(session, observation.h1 or observation.title, observation.url)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "router",
        "select_capability",
        "ok",
        capability="reader.summarize",
        agent=chosen.agent,
        permissions=["read"],
        evidence_ref=evidence,
    )
    decision = decide(summary_control, confirmed=False)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "policy",
        "decide",
        decision.decision,
        risk_class="read",
        permissions=["read"],
    )
    _commit_wir(session, events, trace_id, turn_id)
    _remember_decision(
        session,
        events,
        trace_id,
        turn_id,
        chosen_action="reader.summarize",
        chosen_label="Read the page",
        evidence=evidence,
        alternative_branches=[branch.id for branch in branches],
        risk_class="read",
        policy_outcome=decision.decision,
        expected_postcondition={"oriented": True},
        observed_postcondition={"oriented": True, "family": observation.family},
        confidence="certain" if observation.supported else "partial",
        confirmation_outcome="not_required",
    )
    speech = orientation_speech(wir.page["summary"], branches, session.density)
    if not observation.supported:
        speech = observation.summary
    if prefix:
        speech = f"{prefix} {speech}"
    _publish(session, events, trace_id, turn_id, "branch_ready", "Options ready")
    return _finish(session, speech, turn_id, trace_id, events)


def _commit_browser(
    session: Session,
    control: Control,
    confirmed: bool,
    turn_id: str,
    trace_id: str,
    events: list[dict],
    channel: str,
) -> dict:
    fresh = _observe(session)
    stamp, _, recipe = control.semantic_target.partition("|")
    # The page plan shows one add button, but a goal may name any add button. Each is grounded in this
    # same fresh read, so it is as trustworthy as a listed option and still goes through the policy gate.
    grounded = {item.id: item for item in fresh.controls}
    for extra in add_controls(fresh):
        grounded.setdefault(extra.id, extra)
    current_ids = set(grounded)
    typed = control.id.startswith("enter-") and control.id not in current_ids
    if control.capability.startswith("browser.") and control.id not in current_ids and not typed:
        _emit(session, events, trace_id, turn_id, "orchestrator", "invalid_branch", "rejected", evidence_ref=fresh.evidence_ref)
        _publish(session, events, trace_id, turn_id, "failure", "Option rejected")
        return _finish(session, "That is not one of the current options. The page is unchanged.", turn_id, trace_id, events)
    if fresh.stamp != stamp:
        session.observation = fresh
        _emit(session, events, trace_id, turn_id, "orchestrator", "stale_evidence", "rejected", evidence_ref=fresh.evidence_ref)
        _publish(session, events, trace_id, turn_id, "failure", "Stale option")
        return _finish(session, "That option is out of date. The page was re-read and left unchanged.", turn_id, trace_id, events)
    if control.id in current_ids:
        control = grounded[control.id]
        _, _, recipe = control.semantic_target.partition("|")
    try:
        chosen = route(control)
    except UnknownCapability:
        _emit(session, events, trace_id, turn_id, "router", "select_capability", "rejected", capability=control.capability)
        return _finish(session, "That capability is not in the registry.", turn_id, trace_id, events)
    decision = decide(control, confirmed=confirmed)
    conversation.select_for_control(session, events, trace_id, turn_id, control, chosen.agent, fresh.family)
    conversation.note_policy(session, events, trace_id, turn_id, decision.decision)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "router",
        "select_capability",
        "ok",
        capability=chosen.capability,
        agent=chosen.agent,
        permissions=chosen.permissions,
        evidence_ref=fresh.evidence_ref,
        dom_ref=recipe.split("=")[0],
    )
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "policy",
        "decide",
        decision.decision,
        risk_class=decision.risk_class,
        permissions=decision.permissions,
        reason=decision.reason,
    )
    envelope = AgentEnvelope(
        trace_id=trace_id,
        request_id=uuid.uuid4().hex,
        sender="orchestrator",
        recipient=chosen.agent,
        task_type=control.capability,
        input_refs=[f"wir:{session.id}:{turn_id}", f"browser:{fresh.stamp}"],
        required_capability=control.capability,
        permissions=chosen.permissions,
        risk_class=control.risk_class,
        expected_output_schema="Verification@0.1",
        deadline_ms=10000,
        metadata={"user_confirmed": confirmed, "channel": channel, "primitive": recipe},
    )
    _emit(session, events, trace_id, turn_id, "orchestrator", "agent_envelope", "ok", envelope=envelope.model_dump(mode="json"))
    if decision.decision == "DENY":
        _remember_decision(
            session,
            events,
            trace_id,
            turn_id,
            chosen_action=control.id,
            chosen_label=control.label,
            evidence=fresh.evidence_ref,
            alternative_branches=[branch.id for branch in session.branches if branch.id != control.id],
            risk_class=control.risk_class,
            policy_outcome="DENY",
            expected_postcondition=dict(control.expected_postcondition),
            observed_postcondition={},
            confidence="none",
            confirmation_outcome="denied",
        )
        _publish(session, events, trace_id, turn_id, "failure", "Denied")
        return _finish(session, f"Denied. {decision.reason}", turn_id, trace_id, events)
    if decision.decision == "REQUIRE_CONFIRMATION":
        session.pending = control
        session.observation = fresh
        conversation.note(session, "confirm", f"Asked you to confirm: {control.label}")
        _emit(
            session,
            events,
            trace_id,
            turn_id,
            "policy",
            "confirmation_wait",
            "pending",
            risk_class=control.risk_class,
            confirmation_outcome="waiting",
            evidence_ref=fresh.evidence_ref,
        )
        _remember_decision(
            session,
            events,
            trace_id,
            turn_id,
            chosen_action=control.id,
            chosen_label=control.label,
            evidence=fresh.evidence_ref,
            alternative_branches=[branch.id for branch in session.branches if branch.id != control.id],
            risk_class=control.risk_class,
            policy_outcome="REQUIRE_CONFIRMATION",
            expected_postcondition=dict(control.expected_postcondition),
            observed_postcondition={},
            confidence="unresolved",
            confirmation_outcome="waiting",
        )
        _publish(session, events, trace_id, turn_id, "needs_confirmation", "Waiting for confirmation")
        speech = f"{control.preview} This needs confirmation. Say yes or no."
        return _finish(session, speech, turn_id, trace_id, events)

    started = time.perf_counter()
    result, after = runtime.browser.act(session.id, recipe, session.faults)
    after = _prepare(session, after)
    session.observation = after
    conversation.note_page(session, after.h1 or after.title, after.url)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "executor",
        "apply",
        result.status,
        started=started,
        error_class=result.error_class,
        changed=result.changed,
        evidence_ref=after.evidence_ref,
        evidence_before=fresh.evidence_ref,
        dom_ref=recipe.split("=")[0],
        page_url=after.url,
    )
    verification = verify_browser(
        control, after, density=session.density,
        semantic=capabilities.is_enabled(session.capabilities_off, "semantic_explainer"),
    )
    session.last_verification = verification
    conversation.note_verifier(session, events, trace_id, turn_id, verification.status)
    _emit(
        session,
        events,
        trace_id,
        turn_id,
        "verifier",
        "check_postcondition",
        verification.status,
        expected=verification.expected,
        observed=verification.observed,
        evidence_ref=after.evidence_ref,
        independent_of_receipt=True,
    )
    offered = [branch.id for branch in session.branches if branch.id != control.id]
    controls = after.controls
    snapshot = _browser_snapshot(after, controls)
    wir = interpret(
        snapshot,
        session_id=session.id,
        turn_id=turn_id,
        trace_id=trace_id,
        goal=session.goal,
        channels=runtime.plane.channels(),
        devices=runtime.plane.directory(),
        history_summary=" ".join(session.memory_lines)[-500:],
        density=session.density,
    )
    branches = rank(controls, session.goal.constraints, session.density, session.branch_limit)
    branches = _apply_model(wir, snapshot, controls, session, branches)
    wir.actions = branches
    session.wir = wir
    session.branches = branches
    _emit(session, events, trace_id, turn_id, "interpreter", "reread_wir", "ok", evidence_ref=after.evidence_ref)
    _commit_wir(session, events, trace_id, turn_id)
    observed = dict(verification.observed)
    _remember_decision(
        session,
        events,
        trace_id,
        turn_id,
        chosen_action=control.id,
        chosen_label=control.label,
        evidence=after.evidence_ref,
        alternative_branches=offered,
        risk_class=control.risk_class,
        policy_outcome=decision.decision,
        expected_postcondition=dict(control.expected_postcondition),
        observed_postcondition=observed,
        confidence=_confidence(verification.status, dict(control.expected_postcondition), observed),
        confirmation_outcome="confirmed" if confirmed else "not_required",
    )
    if verification.status == "SUCCESS":
        runtime.remember(session, f"Chose {control.label}.")
        conversation.note(session, "read" if control.informational else "choice", f"{'Read' if control.informational else 'Chose'}: {control.label}.")
        token = "action_verified" if result.changed else "orient"
        label = "Verified state change" if result.changed else "Read verified"
        lead = _browser_lead(control, recipe, verification, after)
        speech = narrate_result(lead, branches, session.density)
    else:
        token = "failure"
        label = "Verification failed"
        if result.error_class == "selector_not_found":
            speech = narrate_result("That control is not on the page. Nothing changed.", branches, session.density)
        else:
            speech = narrate_result(still_on_page(after.summary, after.status), branches, session.density)
    _publish(session, events, trace_id, turn_id, token, label)
    return _finish(session, speech, turn_id, trace_id, events)


def _browser_lead(control: Control, recipe: str, verification: Verification, after: PageObservation) -> str:
    """What the person hears first: the content they asked for, taken from the fresh read."""
    if control.informational:
        return verification.detail
    if recipe.startswith("activate:") and after.answer:
        return f"{after.facts.get('expanded')}: {after.answer}"
    if recipe.startswith("follow:"):
        return after.summary
    if control.capability == "browser.submit" and after.status:
        return after.status
    if recipe.startswith("search:") or control.capability == "browser.filter":
        return after.status or after.summary
    if control.capability == "browser.fill":
        return f"Done: {control.label}."
    return after.summary


def _view(session: Session) -> dict:
    snapshot = build_snapshot(session.page)
    branches = [
        {
            "id": branch.id,
            "label": branch.label,
            "rationale": branch.rationale,
            "capability": branch.capability,
            "risk_class": branch.risk_class,
            "preview": branch.preview,
            "informational": branch.informational,
            "risk_label": RISK_LABELS[branch.risk_class],
        }
        for branch in session.branches
    ]
    pending = None
    if session.pending is not None:
        pending = {
            "id": session.pending.id,
            "label": session.pending.label,
            "preview": session.pending.preview,
            "risk_class": session.pending.risk_class,
            "risk_label": RISK_LABELS[session.pending.risk_class],
        }
    verification = session.last_verification.model_dump(mode="json") if session.last_verification else None
    page = _page_view(session, snapshot)
    repairs = session.observation.repairs if session.mode == "browser" and session.observation is not None else []
    page["repairs"] = repairs
    page["semantics"] = _semantics(session)
    goal = session.goal.model_dump(mode="json")
    state = semantic.build_state(
        turn_id=session.last_turn_id,
        page=page,
        mode=session.mode,
        branches=branches,
        pending=pending,
        verification=verification,
        goal=goal,
        goal_plan=session.goal_plan,
        journey=session.journey,
        uncertainty=list(session.wir.uncertainty) if session.wir else [],
        repairs=repairs,
        density=session.density,
        selected=session.selected,
    )
    braille = _braille_view(session.last_speech)
    motor_ran = bool(session.tactile_cue and session.tactile_cue.get("physical_motor_ran"))
    p4 = runtime.plane.boards[P4_ID]
    p4_connected = bool(p4.connected and not p4.simulated and not p4.loopback)
    devices = [runtime.plane.entry(board_id) for board_id in (DEVKIT_ID, BOX3_ID, P4_ID)]
    compiled = semantic.compile_channels(
        state=state, speech=session.last_speech, braille=braille, tactile=dict(session.tactile), muted=session.speech_muted
    )
    return {
        "session_id": session.id,
        "turn_id": session.last_turn_id,
        "trace_id": session.last_trace_id,
        "speech": session.last_speech,
        "braille": braille,
        "wir": session.wir.model_dump(mode="json") if session.wir else None,
        "branches": branches,
        "verification": verification,
        "decision_record": session.last_decision,
        "pending_confirmation": pending,
        "page": page,
        "tactile": session.tactile,
        **_tactile_demo(session),
        "devices": devices,
        "box3_display": session.box3_display,
        "evidence": list(session.evidence),
        "events": session.last_events,
        "density": session.density,
        "goal": goal,
        "history_summary": " ".join(session.memory_lines),
        "adr": ADR_ID,
        "orchestrator": "mac",
        "mode": session.mode,
        "target": session.browser_target or "fixture",
        "adapter": "playwright" if session.mode == "browser" else "harbor_fixture",
        "hardware_note": HARDWARE_NOTE,
        "glance": semantic.glance_from_state(state),
        "semantic_state": state,
        "compiled": compiled,
        "channels": semantic.channels_view(
            renderer=session.renderer,
            muted=session.speech_muted,
            status=session.channel_status,
            plane_channels=runtime.plane.channels(),
            p4_connected=p4_connected,
        ),
        "renderer": session.renderer,
        "speech_muted": session.speech_muted,
        "capabilities": capabilities.registry_view(session.capabilities_off, session.selected_ids, session.selected),
        "selected": session.selected,
        "selections": list(session.selection_log),
        "goal_plan": conversation.plan_view(session),
        "journey": list(session.journey[-30:]),
        "memory": {
            "session_path": len(session.journey),
            "project": session.project,
            "projects": conversation.projects(),
            "preferences": {
                "density": session.density,
                "branch_limit": session.branch_limit,
                "preferred_channels": list(session.preferred_channels),
            },
            "raw_audio_or_video_stored": False,
            "scope": "Session path, saved projects, and preferences stay on this Mac. No audio or video is ever stored.",
        },
        "hardware": semantic.hardware_view(physical_motor_ran=motor_ran, devices=devices, p4_connected=p4_connected),
    }


def _semantics(session: Session) -> dict | None:
    if session.mode != "browser" or session.observation is None or not session.observation.supported:
        return None
    if not capabilities.is_enabled(session.capabilities_off, "semantic_explainer"):
        return {"text": "", "has_semantics": False, "limit": "The Semantic Explainer capability is turned off."}
    from wayfinder.explain import semantic_explanation

    return semantic_explanation(session.observation, session.observation.controls, session.density)


def set_capability(session_id: str, capability_id: str, enabled: bool) -> dict:
    with runtime.lock:
        session = _session(session_id)
        try:
            entry = capabilities.get(capability_id)
        except capabilities.UnknownCapabilityId as exc:
            raise TopologyError("unknown_capability") from exc
        if entry.kind == "core":
            raise TopologyError("capability_locked")
        if enabled:
            session.capabilities_off.discard(capability_id)
        else:
            session.capabilities_off.add(capability_id)
        if session.observation is not None:
            session.observation.specialists_off = frozenset(
                item for item in session.capabilities_off if item in capabilities.FAMILY_TO_ID.values()
            )
        conversation.note(session, "capability", f"{entry.name} turned {'on' if enabled else 'off'}")
        return _view(session)


def set_renderer(session_id: str, renderer: str) -> dict:
    with runtime.lock:
        session = _session(session_id)
        if renderer not in semantic.RENDERERS:
            raise TopologyError("unknown_renderer")
        text = conversation.set_renderer(session, renderer)
        # Status only. The goal, the path, the pending confirmation, and the branches are untouched.
        session.last_speech = text
        conversation.select(session, [], session.last_trace_id or "renderer", session.last_turn_id or "renderer", "orchestrator", f"output switched to {renderer}")
        return _view(session)


def set_preferences(session_id: str, speech_muted: bool | None, density: str | None, branch_limit: int | None) -> dict:
    with runtime.lock:
        session = _session(session_id)
        if speech_muted is not None:
            session.speech_muted = speech_muted
        if density is not None:
            if density not in {"brief", "standard", "full"}:
                raise TopologyError("invalid_payload")
            session.density = density  # type: ignore[assignment]
            conversation.persist_density(session)
        if branch_limit is not None:
            if branch_limit not in {3, 5}:
                raise TopologyError("invalid_payload")
            session.branch_limit = branch_limit
            runtime.memory.set_preference("branch_limit", str(branch_limit))
        return _view(session)


def set_channel_status(session_id: str, mic: str | None, camera: str | None) -> dict:
    """Status words only ('armed', 'muted', 'off'). No audio or video ever reaches the Mac."""
    allowed = {"available", "armed", "muted", "off", "blocked", "unsupported"}
    with runtime.lock:
        session = _session(session_id)
        for name, value in (("mic", mic), ("camera", camera)):
            if value is None:
                continue
            if value not in allowed:
                raise TopologyError("invalid_payload")
            session.channel_status[name] = value
        return _view(session)


def save_project(session_id: str, name: str) -> dict:
    with runtime.lock:
        session = _session(session_id)
        session.last_speech = conversation.save_project(session, name)
        return _view(session)


def continue_project(session_id: str, name: str) -> dict:
    with runtime.lock:
        session = _session(session_id)
        turn_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
        conversation.begin_turn(session)
        return conversation.continue_project(session, name, turn_id, trace_id, [])


_install_board_callback()
