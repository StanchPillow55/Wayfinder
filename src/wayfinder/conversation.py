"""Interruptions, memory, goals, and the output switch.

These handlers sit beside the turn loop in orchestrator.py. They never touch a page
directly: a goal step is an ordinary grounded Control that goes through the same
_commit path (router, policy gate, navigator, verifier) as a tapped choice, so
"just do it for me" and a goal can never skip a confirmation.

Memory has three kinds, all local and with no account:
  1. session path   - goal, constraints, pages, choices, confirmations (session.journey)
  2. named projects - MemoryCurator.workspaces, continued by name
  3. preference bank - MemoryCurator.preferences: concise or full, about 3 options,
     preferred channels. Preferences only. Never a diagnosis, never audio or images.
"""

from __future__ import annotations

import time

from wayfinder import capabilities, goals
from wayfinder.memory import MemoryError
from wayfinder.models import Control, Intent
from wayfinder.semantic import RENDERERS
from wayfinder.state import Session, runtime

JOURNEY_LIMIT = 60
HELP_BY_DENSITY = {"brief": 3, "standard": 6, "full": 12}


# ---------------------------------------------------------------- selection (the visible router line)


def begin_turn(session: Session) -> None:
    session.prev_selections = list(session.selection_log)
    session.selection_log = []
    session.selected_ids = []
    session.selected = None


def select(
    session: Session,
    events: list[dict],
    trace_id: str,
    turn_id: str,
    capability_id: str,
    reason: str,
    *,
    via: str | None = None,
    headline: bool = True,
) -> dict:
    """Record which registry entry took this step. The UI shows the headline entry."""
    from wayfinder import orchestrator as orch

    item = capabilities.selection(capability_id, reason, via)
    if capability_id not in session.selected_ids:
        session.selected_ids.append(capability_id)
    if via and via not in session.selected_ids:
        session.selected_ids.append(via)
    session.selection_log.append(item)
    session.selection_log = session.selection_log[-12:]
    if headline:
        session.selected = item
    orch._emit(
        session, events, trace_id, turn_id, "router", "capability_selected", "ok",
        capability_id=capability_id, via=via, reason=reason[:120], trust=item["trust"],
    )
    return item


def select_for_control(
    session: Session, events: list[dict], trace_id: str, turn_id: str, control: Control, agent: str, family: str
) -> None:
    """Router choice for one grounded control. Specialists run behind the navigator, never instead of it."""
    base = capabilities.AGENT_TO_ID.get(agent, "reader")
    if control.capability.startswith("reader."):
        select(session, events, trace_id, turn_id, "reader", _reason(control))
        return
    specialist = capabilities.specialist_for_family(family)
    if specialist and capabilities.is_enabled(session.capabilities_off, specialist):
        select(session, events, trace_id, turn_id, specialist, _reason(control), via=base)
    else:
        select(session, events, trace_id, turn_id, base, _reason(control))


def _reason(control: Control) -> str:
    return control.label[:90]


def note_policy(session: Session, events: list[dict], trace_id: str, turn_id: str, outcome: str) -> None:
    words = {"ALLOW": "allow", "REQUIRE_CONFIRMATION": "confirm", "DENY": "deny"}
    select(session, events, trace_id, turn_id, "policy_gate", words.get(outcome, outcome.lower()), headline=False)


def note_verifier(session: Session, events: list[dict], trace_id: str, turn_id: str, status: str) -> None:
    select(session, events, trace_id, turn_id, "verifier", status.lower(), headline=False)


# ---------------------------------------------------------------- memory kind 1: the session path


def note(session: Session, kind: str, text: str, url: str = "") -> None:
    if not capabilities.is_enabled(session.capabilities_off, "memory_agent"):
        return
    clean = " ".join(text.split())[:200]
    if not clean:
        return
    entry = {"seq": len(session.journey) + 1, "kind": kind, "text": clean, "url": url[:200], "at_ms": int(time.time() * 1000)}
    session.journey.append(entry)
    del session.journey[:-JOURNEY_LIMIT]
    try:
        runtime.memory.add(session.id, f"[path:{kind}] {clean}")
    except MemoryError:
        pass
    except Exception:  # noqa: BLE001 - the session path still stands if sqlite is unavailable
        pass


def note_page(session: Session, title: str, url: str) -> None:
    if url and url != session.journey_url:
        session.journey_url = url
        note(session, "page", f"Opened {title or url}", url)


def where_was_i(session: Session, title: str) -> str:
    if not capabilities.is_enabled(session.capabilities_off, "memory_agent"):
        return "Memory is turned off in the capability list, so I kept no path. Turn Memory on to use this."
    limit = HELP_BY_DENSITY.get(session.density, 6)
    steps = session.journey[-limit:]
    parts = [f"You are on {title}." if title else "You are on this page."]
    if session.goal.text:
        parts.append(f"Your goal: {session.goal.text}.")
    if session.goal.constraints:
        parts.append("Constraints: " + ", ".join(session.goal.constraints) + ".")
    if steps:
        omitted = len(session.journey) - len(steps)
        lead = f"Path, last {len(steps)} of {len(session.journey)}:" if omitted else "Path:"
        parts.append(lead + " " + " Then ".join(item["text"].rstrip(".") + "." for item in steps))
    else:
        parts.append("Nothing else has happened yet.")
    if session.project:
        parts.append(f"This is part of your saved project {session.project}.")
    return " ".join(parts)


# ---------------------------------------------------------------- memory kinds 2 and 3


def apply_bank(session: Session) -> None:
    """Start from the personalization bank. Preferences only."""
    try:
        bank = runtime.memory.preferences()
    except Exception:  # noqa: BLE001
        return
    if bank.get("density") in {"brief", "standard", "full"}:
        session.density = bank["density"]  # type: ignore[assignment]
    if bank.get("branch_limit") in {"3", "5"}:
        session.branch_limit = int(bank["branch_limit"])
    if bank.get("preferred_channels"):
        session.preferred_channels = bank["preferred_channels"].split(",")


def persist_density(session: Session) -> None:
    try:
        runtime.memory.set_preference("density", session.density)
    except MemoryError:
        pass


def inherit(session: Session, previous: Session) -> None:
    """Continue one browsing session across a new page open. Goal, path, and choices carry over."""
    session.density = previous.density
    session.branch_limit = previous.branch_limit
    session.preferred_channels = list(previous.preferred_channels)
    session.renderer = previous.renderer
    session.speech_muted = previous.speech_muted
    session.capabilities_off = set(previous.capabilities_off)
    session.goal = previous.goal.model_copy(deep=True)
    session.journey = [dict(item) for item in previous.journey]
    session.journey_url = previous.journey_url
    session.project = previous.project
    session.channel_status = dict(previous.channel_status)
    session.memory_lines = list(previous.memory_lines)
    if previous.goal_plan:
        session.goal_plan = {**previous.goal_plan, "visited": []}
        if session.goal_plan.get("status") == "waiting_confirmation":
            session.goal_plan["status"] = "paused"


def _workspace_body(session: Session) -> dict:
    url = ""
    if session.mode == "browser" and session.observation is not None and session.browser_target == "url":
        url = session.observation.url
    return {
        "goal": session.goal.model_dump(mode="json"),
        "journey": [{"kind": item["kind"], "text": item["text"]} for item in session.journey[-30:]],
        "target": session.browser_target or "fixture",
        "url": url,
        "density": session.density,
        "branch_limit": session.branch_limit,
        "goal_plan": {"text": session.goal_plan["text"], "status": session.goal_plan.get("status")} if session.goal_plan else None,
    }


def save_project(session: Session, name: str) -> str:
    try:
        runtime.memory.save_workspace(name, _workspace_body(session))
    except MemoryError as exc:
        return f"I could not save that project ({exc}). Nothing was stored."
    session.project = " ".join(name.split()).lower()
    note(session, "project", f"Saved the project {session.project}")
    return (
        f"Saved this as the project {session.project}: your goal, constraints, and path. "
        "The page itself is not saved, and no audio or video is stored. Say continue and its name to pick it up later."
    )


def projects() -> list[str]:
    try:
        return runtime.memory.workspaces()
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- the output switch


def set_renderer(session: Session, renderer: str) -> str:
    if renderer not in RENDERERS:
        raise ValueError("unknown_renderer")
    session.renderer = renderer
    spec = RENDERERS[renderer]
    session.speech_muted = not spec["audio"]
    if renderer == "tactile":
        tail = "Audio is off. Braille, the tactile cue, and the switches work."
    elif renderer == "visual":
        tail = "Audio is off. Cards, Braille, and the switches still work."
    elif renderer == "hands_free":
        tail = "Voice and camera are armed. Press once in the browser if it asks for permission."
    elif renderer == "audio":
        tail = "Audio is on."
    else:
        tail = "Every output is on."
    return f"Output set to {spec['label']}. {tail} Your goal and your path are kept."


# ---------------------------------------------------------------- goals


def _plan_view(plan: dict | None) -> dict | None:
    if not plan:
        return None
    return {
        "text": plan["text"],
        "status": plan.get("status", "planned"),
        "steps": list(plan.get("steps") or []),
        "waiting_for": plan.get("waiting_for", ""),
        "next": plan.get("next", ""),
        "limit": "Same-origin links only. A consequential step always waits for your yes.",
    }


def plan_view(session: Session) -> dict | None:
    return _plan_view(session.goal_plan)


def start_goal(session: Session, intent: Intent, turn_id: str, trace_id: str, events: list[dict], channel: str) -> dict:
    from wayfinder import orchestrator as orch

    if not capabilities.is_enabled(session.capabilities_off, "goal_planner"):
        return orch._finish(
            session,
            "The Goal Planner capability is turned off. Turn it on in the capability list, or choose a number.",
            turn_id, trace_id, events,
        )
    spec = goals.parse_goal(intent.text)
    if spec is None:
        return orch._finish(
            session,
            "I could not turn that into a goal. Try: find the hours, request the north room, or add the talking kitchen scale to my cart.",
            turn_id, trace_id, events,
        )
    interrupt = intent.field == "interrupt"
    previous = session.goal_plan["text"] if session.goal_plan else ""
    if session.pending is not None:
        # A new priority replaces a waiting confirmation. Nothing was submitted.
        session.pending = None
    session.goal.text = spec.text
    session.goal_plan = {
        "text": spec.text, "status": "planned", "steps": [], "visited": [], "added": False,
        "searched": False, "used": [], "waiting_for": "", "next": "", "origin": session.observation.url if session.observation else "",
    }
    note(session, "goal", f"New priority: {spec.text}" if interrupt else f"Goal: {spec.text}")
    select(session, events, trace_id, turn_id, "goal_planner", spec.text[:90])
    if session.mode != "browser":
        session.goal_plan["status"] = "saved"
        return orch._finish(
            session,
            f"Saved your goal: {spec.text}. Goal steps need a live browser page, and this is the Harbor Line fixture, so I changed nothing.",
            turn_id, trace_id, events,
        )
    lead = (
        f"New priority noted. Your path so far is kept{', and I set aside ' + previous if previous else ''}. "
        if interrupt else ""
    )
    return advance_goal(session, turn_id, trace_id, events, channel, lead)


def advance_goal(
    session: Session, turn_id: str, trace_id: str, events: list[dict], channel: str, lead: str = ""
) -> dict:
    """Run grounded steps until an answer, a missing value, a confirmation, or the step limit."""
    from wayfinder import orchestrator as orch

    plan = session.goal_plan
    if plan is None:
        return orch._finish(session, "There is no goal yet. Say, for example: find the hours.", turn_id, trace_id, events)
    if session.mode != "browser":
        return orch._finish(session, "Goal steps need a live browser page.", turn_id, trace_id, events)
    spec = goals.parse_goal(plan["text"])
    if spec is None:
        return orch._finish(session, "I lost the thread of that goal. Please say it again.", turn_id, trace_id, events)
    lines: list[str] = []
    plan["status"] = "running"
    plan["waiting_for"] = ""
    for number in range(1, goals.MAX_AUTO_STEPS + 1):
        observation = orch._observe(session)
        session.observation = observation
        if observation.url not in plan["visited"]:
            plan["visited"].append(observation.url)
        decision = goals.decide(
            spec, observation, observation.controls,
            {**{k: plan.get(k) for k in ("visited", "added", "searched", "used")}, "steps_done": len(plan["steps"])},
        )
        select(session, events, trace_id, turn_id, "goal_planner", decision.text[:90], headline=number == 1)
        if decision.kind == "answer":
            plan["status"] = "done"
            lines.append(f"Found on {observation.h1}: {decision.text}")
            break
        if decision.kind == "done":
            plan["status"] = "done"
            lines.append(decision.text)
            break
        if decision.kind == "need_input":
            plan["status"] = "need_input"
            plan["waiting_for"] = decision.field
            lines.append(decision.text)
            break
        if decision.kind == "stuck" or decision.control is None:
            plan["status"] = "stuck"
            lines.append(decision.text)
            break
        control = decision.control
        plan["next"] = decision.text
        step = {"n": len(plan["steps"]) + 1, "label": control.label, "status": "running"}
        plan["steps"].append(step)
        orch._commit(session, control, False, turn_id, trace_id, events, channel)
        verification = session.last_verification
        if session.pending is not None:
            step["status"] = "waiting_confirmation"
            plan["status"] = "waiting_confirmation"
            lines.append(f"Step {number}: {control.label}. {control.preview} This needs your yes or no.")
            break
        ok = verification is not None and verification.status == "SUCCESS"
        step["status"] = "verified" if ok else (verification.status.lower() if verification else "failed")
        if not ok:
            plan["status"] = "stuck"
            lines.append(f"Step {number}: {control.label}. It did not verify, so I stopped. Nothing else was done.")
            break
        recipe = control.semantic_target.partition("|")[2]
        if control.id.startswith("add-"):
            plan["added"] = True
        if recipe.startswith("search:") or control.id.startswith("enter-search"):
            plan["searched"] = True
        if recipe.startswith("filter:"):
            plan["used"].append(control.id)
        lines.append(f"Step {number}: {control.label}. Verified.")
    else:
        plan["status"] = "paused"
        lines.append(f"I took {goals.MAX_AUTO_STEPS} steps and paused. Say continue to go on, or stop.")
    plan["next"] = ""
    head = f"{lead}Goal: {plan['text']}. "
    tail = ""
    if plan["status"] in {"done", "need_input", "stuck", "paused"} and session.branches:
        from wayfinder.renderers import narrate_result

        tail = " " + narrate_result("", session.branches, session.density).strip()
    return orch._finish(session, f"{head}{' '.join(lines)}{tail}".strip(), turn_id, trace_id, events)


def after_confirm(session: Session, control: Control) -> bool:
    """A confirmed consequential step finishes a goal that was waiting for it."""
    plan = session.goal_plan
    if not plan or plan.get("status") != "waiting_confirmation":
        return False
    ok = session.last_verification is not None and session.last_verification.status == "SUCCESS"
    plan["status"] = "done" if ok else "stuck"
    if plan["steps"]:
        plan["steps"][-1]["status"] = "verified" if ok else "failed"
    if ok:
        note(session, "goal", f"Goal finished: {plan['text']}")
    return True


def pause_goal(session: Session) -> None:
    if session.goal_plan and session.goal_plan.get("status") in {"running", "planned", "waiting_confirmation", "need_input"}:
        session.goal_plan["status"] = "paused"


# ---------------------------------------------------------------- the dispatcher


def handle(session: Session, intent: Intent, turn_id: str, trace_id: str, events: list[dict], channel: str) -> dict | None:
    """Return a view when this intent is one of the conversation intents, else None."""
    from wayfinder import orchestrator as orch

    kind = intent.type
    page_title = orch.current_title(session)

    if kind == "wait":
        pause_goal(session)
        note(session, "stop", "Waited")
        extra = " A confirmation is still waiting. Say yes or no." if session.pending else ""
        return orch._finish(
            session, f"Waiting. Nothing changed and your place is kept. Say continue when you are ready.{extra}",
            turn_id, trace_id, events, meta=True,
        )
    if kind == "repeat":
        said = session.last_substantive_speech
        text = f"I said: {said}" if said else "I have not said anything yet."
        return orch._finish(session, text, turn_id, trace_id, events, meta=True)
    if kind == "explain_that":
        select(session, events, trace_id, turn_id, "reader", "explain the last decision")
        return orch._finish(session, explain_that(session), turn_id, trace_id, events, meta=True)
    if kind == "who_acting":
        previous = getattr(session, "prev_selections", [])
        if not previous:
            text = "Nothing has been selected yet. Ask what is here, and I will name who answered."
        else:
            text = "On the last step: " + " ".join(item["line"] + "." for item in previous[:5])
        return orch._finish(session, text, turn_id, trace_id, events, meta=True)
    if kind == "where_was_i":
        select(session, events, trace_id, turn_id, "memory_agent", "session path")
        return orch._finish(session, where_was_i(session, page_title), turn_id, trace_id, events, meta=True)
    if kind == "read_all":
        old = session.density
        session.density = "full"
        try:
            orch._reorient(session, turn_id, trace_id, events, "Reading everything.")
        finally:
            session.density = old
        extra = ""
        if session.mode == "browser" and session.observation is not None and session.observation.fulltext:
            extra = " Page text: " + " ".join(session.observation.fulltext.split())[:600]
        return orch._finish(session, session.last_speech + extra, turn_id, trace_id, events)
    if kind == "forget_constraint":
        return forget(session, intent.value, turn_id, trace_id, events)
    if kind == "just_do_it":
        if session.pending is not None:
            return orch._finish(
                session,
                f"{session.pending.preview} That needs your own yes or no. I did not confirm anything for you.",
                turn_id, trace_id, events, meta=True,
            )
        if session.goal_plan is not None and session.goal_plan.get("status") == "done":
            return orch._finish(
                session, "That goal is finished. Tell me a new goal, or choose a number.", turn_id, trace_id, events, meta=True
            )
        if session.goal_plan is not None:
            select(session, events, trace_id, turn_id, "goal_planner", "carry on with the goal")
            return advance_goal(session, turn_id, trace_id, events, channel)
        if session.goal.text:
            session.goal_plan = {
                "text": session.goal.text, "status": "planned", "steps": [], "visited": [], "added": False,
                "searched": False, "used": [], "waiting_for": "", "next": "", "origin": "",
            }
            select(session, events, trace_id, turn_id, "goal_planner", session.goal.text[:90])
            return advance_goal(session, turn_id, trace_id, events, channel)
        return orch._finish(
            session, "Tell me the goal first, for example: find the hours. I will take the safe steps and stop for your yes.",
            turn_id, trace_id, events, meta=True,
        )
    if kind == "set_goal":
        return start_goal(session, intent, turn_id, trace_id, events, channel)
    if kind == "mute":
        session.speech_muted = True
        return orch._finish(
            session, "Audio is muted. The screen, Braille, and switches still work, and your task is unchanged.",
            turn_id, trace_id, events, meta=True,
        )
    if kind == "unmute":
        session.speech_muted = False
        return orch._finish(session, "Audio is on.", turn_id, trace_id, events, meta=True)
    if kind == "set_renderer":
        text = set_renderer(session, str(intent.value))
        select(session, events, trace_id, turn_id, "orchestrator", f"output switched to {intent.value}")
        return orch._finish(session, text, turn_id, trace_id, events, meta=True)
    if kind == "save_project":
        select(session, events, trace_id, turn_id, "memory_agent", "save project")
        return orch._finish(session, save_project(session, str(intent.value)), turn_id, trace_id, events, meta=True)
    if kind == "continue_project":
        return continue_project(session, str(intent.value), turn_id, trace_id, events)
    if kind == "set_preference":
        return set_preference(session, str(intent.field), str(intent.value), turn_id, trace_id, events)
    if kind == "reset_preferences":
        runtime.memory.reset_preferences()
        session.branch_limit = 5
        session.density = "standard"
        session.preferred_channels = []
        return orch._reorient(session, turn_id, trace_id, events, "Preferences reset to the defaults.")
    return None


def explain_that(session: Session) -> str:
    if session.pending is not None:
        pending = session.pending
        return (
            f"{pending.preview} The policy gate asked for your yes because this is a {pending.risk_class} action. "
            "Nothing has been done yet."
        )
    record = session.last_decision or session.last_explained
    if not record:
        return "There is nothing to explain yet. Ask what is here first."
    status = record.get("verification") or (session.last_verification.status if session.last_verification else "not checked")
    others = ", ".join(record.get("alternative_branches") or []) or "none"
    return (
        f"I chose {record['chosen_label']}. Policy said {record['policy_outcome']}. "
        f"Risk class {record['risk_class']}. The page re-read result was {status}. "
        f"Evidence: {record['evidence']}. Other options were: {others}."
    )


def forget(session: Session, name: str | None, turn_id: str, trace_id: str, events: list[dict]) -> dict:
    from wayfinder import orchestrator as orch

    constraints = session.goal.constraints
    if not constraints:
        return orch._finish(session, "There is no constraint to forget.", turn_id, trace_id, events, meta=True)
    target = None
    if name:
        wanted = name.replace("-", " ").replace("_", " ")
        target = next((c for c in constraints if c.replace("_", " ") in wanted or wanted in c.replace("_", " ")), None)
        if target is None:
            return orch._finish(session, f"I have no constraint called {name}.", turn_id, trace_id, events, meta=True)
    target = target or constraints[-1]
    constraints.remove(target)
    note(session, "constraint", f"Forgot the {target.replace('_', ' ')} constraint")
    select(session, events, trace_id, turn_id, "memory_agent", "forget constraint")
    return orch._reorient(
        session, turn_id, trace_id, events,
        f"Forgot the {target.replace('_', ' ')} constraint. Your goal and your path are unchanged.",
    )


def set_preference(session: Session, key: str, value: str, turn_id: str, trace_id: str, events: list[dict]) -> dict:
    from wayfinder import orchestrator as orch

    try:
        if key == "branch_limit":
            session.branch_limit = int(value)
            runtime.memory.set_preference("branch_limit", value)
            prefix = f"I will show about {value} options. Saved as a preference on this Mac."
        else:
            channels = [item for item in session.preferred_channels if item != value] + [value]
            session.preferred_channels = channels[-3:]
            runtime.memory.set_preference("preferred_channels", ",".join(session.preferred_channels))
            prefix = f"Noted: you prefer {value}. All channels stay available. Saved as a preference on this Mac."
    except MemoryError:
        return orch._finish(session, "I can only keep a few simple preferences, and that is not one of them.", turn_id, trace_id, events, meta=True)
    select(session, events, trace_id, turn_id, "memory_agent", "preference")
    return orch._reorient(session, turn_id, trace_id, events, prefix)


def continue_project(session: Session, name: str, turn_id: str, trace_id: str, events: list[dict]) -> dict:
    from wayfinder import orchestrator as orch
    from wayfinder.models import Goal

    body = runtime.memory.load_workspace(name)
    if body is None:
        saved = projects()
        tail = f" Saved projects: {', '.join(saved)}." if saved else " No project has been saved yet. Say: save this as, then a name."
        return orch._finish(session, f"I have no project called {name}.{tail}", turn_id, trace_id, events, meta=True)
    select(session, events, trace_id, turn_id, "memory_agent", f"continue {body['name']}")
    session.goal = Goal.model_validate(body.get("goal") or {})
    session.project = body["name"]
    session.density = body.get("density", session.density)
    session.branch_limit = int(body.get("branch_limit", session.branch_limit))
    for item in body.get("journey") or []:
        session.journey.append({"seq": len(session.journey) + 1, "kind": item.get("kind", "page"), "text": item.get("text", ""), "url": "", "at_ms": 0})
    del session.journey[:-JOURNEY_LIMIT]
    note(session, "project", f"Continued the project {body['name']}")
    plan = body.get("goal_plan")
    session.goal_plan = (
        {"text": plan["text"], "status": "paused", "steps": [], "visited": [], "added": False, "searched": False,
         "used": [], "waiting_for": "", "next": "", "origin": ""}
        if plan else None
    )
    orch.open_target(session, body.get("target") or "fixture", body.get("url") or None)
    goal_line = f" Your goal was: {session.goal.text}." if session.goal.text else ""
    more = " Say continue to go on with it." if session.goal_plan else ""
    return orch._reorient(
        session, turn_id, trace_id, events,
        f"Continuing the project {body['name']}.{goal_line}{more} Page contents are not saved, so the page is read fresh.",
    )
