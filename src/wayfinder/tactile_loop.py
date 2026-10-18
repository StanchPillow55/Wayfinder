"""Tactile loop: Braille word cues out, DevKit buttons and analog in.

DEMO MAP, NOT A CLINICAL DEVICE. This does not prove the motor rotated and it does
not prove a blind user can finish a task. The plate is a software view, not a
physical Braille device.

Out: when the web plate forms a word, the browser asks for one short cue. The cue
reuses the frozen pose token ``orient`` ("something is being shown"), direction
forward, duty at or below the ``orient`` run, a duration that grows with the word
length but never passes the ``orient`` run. It goes through DevicePlane as a normal
``tactile.render``, so a connected DevKit receives exactly that and a simulator or
missing port does not. Nothing here invents a pose name.

In: the DevKit (or an on-screen stand-in) sends one of these. Buttons and the
analog level are UNVERIFIED hardware inputs.

    button A  -> touch.event {"demo_input": "next"}      next branch (moves the highlight)
    button B  -> touch.event {"control": "confirm"}      confirm (existing shape)
    button C  -> touch.event {"demo_input": "back"}      back; held ~0.8 s ->
                 touch.event {"control": "stop"}         stop (existing shape)
    analog    -> touch.event {"demo_input": "analog", "level": 0..100}
                 low to high moves the highlight along the branch list

Protocol stays 0.1 and no message type is added. ``demo_input`` is one additive
touch.event shape (see models._validate_payload). The orchestrator accepts it from
devkit_tactile only.

``physical_motor_ran`` in a cue stays False unless a real (non-simulator,
non-loopback) serial link returns a board ack with status ``ok`` for that
``tactile.render``. A health ping is never a motor run, a ``motor_disabled`` ack
from the health-only build is not a motor run, and even an ``ok`` ack means the
board accepted the command, not that anything was seen to rotate.
"""

from __future__ import annotations

import re
from typing import Literal

from wayfinder.contracts import ACTUATOR, DEVKIT_ID, MOTOR_RUNS
from wayfinder.devices import DispatchResult
from wayfinder.models import TopologyError
from wayfinder.renderers import tactile_payload
from wayfinder.state import Session, runtime

CUE_TOKEN = "orient"  # existing pose that already means "something is being shown"
CUE_DUTY_PERCENT = MOTOR_RUNS[CUE_TOKEN]["duty_percent"]
CUE_MAX_MS = MOTOR_RUNS[CUE_TOKEN]["duration_ms"]
CUE_MIN_MS = 75
CUE_BASE_MS = 50
CUE_PER_CHAR_MS = 25
MAX_WORD_CHARS = 40

InputKind = Literal["next", "confirm", "back", "stop", "analog"]
INPUT_KINDS = ("next", "confirm", "back", "stop", "analog")

DEMO_MAP = {
    "next": "Button A: next branch",
    "confirm": "Button B: confirm",
    "back": "Button C: back",
    "stop": "Button C held: stop",
    "analog": "Analog: move along the branch list, low to high",
}

_WORD = re.compile(r"^[^\x00-\x1f\x7f]+$")


def cue_duration_ms(word: str) -> int:
    return max(CUE_MIN_MS, min(CUE_MAX_MS, CUE_BASE_MS + CUE_PER_CHAR_MS * len(word)))


def _clean_word(word: object) -> str:
    if not isinstance(word, str):
        raise TopologyError("invalid_payload")
    cleaned = word.strip()
    if not cleaned or len(cleaned) > MAX_WORD_CHARS or not _WORD.match(cleaned):
        raise TopologyError("invalid_payload")
    return cleaned


def _motor_note(status: str, board, result: DispatchResult) -> str:
    if status == "disconnected" or not board.connected:
        return "No DevKit is connected. The software cue is shown here and the motor has not spun."
    if status == "lockout":
        return "The actuator is disabled. The cue is shown here and the motor has not spun."
    if board.simulated:
        return "The simulator accepted this cue. No board is attached and the motor has not spun."
    if board.loopback:
        return "A loopback stand-in accepted this cue. No physical board answered and the motor has not spun."
    if status == "motor_disabled":
        return "The DevKit answered from the health-only build. Its motor pin is untouched and the motor has not spun."
    if result.acknowledged:
        return "The DevKit accepted this command on USB. That does not prove the motor rotated."
    return f"The DevKit did not accept this cue ({status}). The motor has not spun."


def publish_word_cue(session_id: str, word: object, index: int = 0, count: int = 1) -> dict:
    """Send one bounded cue for a word the plate just formed. Never claims rotation."""
    from wayfinder import orchestrator as orch

    cleaned = _clean_word(word)
    with runtime.lock:
        session = orch._session(session_id)
        plane = runtime.plane
        board = plane.boards[DEVKIT_ID]
        payload = tactile_payload(CUE_TOKEN, "Word cue")
        payload["duty_percent"] = CUE_DUTY_PERCENT
        payload["duration_ms"] = cue_duration_ms(cleaned)
        payload["direction"] = "forward"
        trace_id = f"cue-{session.last_trace_id or session.id}"[:80]
        sequence = None
        if not board.connected or not board.actuator_enabled:
            status = "disconnected" if not board.connected else "lockout"
            result = DispatchResult(
                status=status,
                acknowledged=False,
                simulated=board.simulated,
                fallback="software",
                latency_ms=0,
                device_angle=board.last_angle,
            )
        else:
            message = plane.command(DEVKIT_ID, "tactile.render", payload, trace_id=trace_id)
            sequence = message.sequence
            result = plane.dispatch(message)
        ack_payload = (result.ack or {}).get("payload", {}) if isinstance(result.ack, dict) else {}
        real_link = board.connected and not board.simulated and not board.loopback and DEVKIT_ID in plane.links
        ack_status = ack_payload.get("status") if isinstance(ack_payload, dict) else None
        motor_ran = bool(real_link and board.physical_ack and result.acknowledged and ack_status == "ok")
        cue = {
            "word": cleaned,
            "word_index": max(0, int(index)),
            "word_count": max(1, int(count)),
            "state_token": CUE_TOKEN,
            "actuator": ACTUATOR,
            "duty_percent": int(payload["duty_percent"]),
            "duration_ms": int(payload["duration_ms"]),
            "direction": "forward",
            "transport": "simulator" if board.simulated else board.link_kind,
            "device_status": board.status,
            "dispatch_status": result.status,
            "board_ack_status": ack_status,
            "acknowledged": result.acknowledged,
            "simulated": bool(board.simulated and board.connected),
            "loopback": board.loopback,
            "sequence": sequence,
            "physical_motor_ran": motor_ran,
            "note": _motor_note(result.status, board, result),
            "at_ms": plane.now_ms(),
        }
        session.tactile_cue = cue
        orch._emit(
            session,
            [],
            trace_id,
            session.last_turn_id or "cue",
            "device",
            "word_cue",
            result.status,
            device_id=DEVKIT_ID,
            word_chars=len(cleaned),
            duty_percent=cue["duty_percent"],
            duration_ms=cue["duration_ms"],
            transport_status=result.status,
            physical_motor_ran=motor_ran,
        )
        return cue


# ---- input: buttons and analog -------------------------------------------------


def board_origin(board) -> str:
    if board.simulated:
        return "simulated"
    if board.loopback:
        return "loopback"
    return "board_usb"


def cursor_view(session: Session) -> dict | None:
    cursor = session.branch_cursor
    if not cursor or cursor.get("turn_id") != session.last_turn_id:
        return None
    index = cursor.get("index")
    if not isinstance(index, int) or not 0 <= index < len(session.branches):
        return None
    branch = session.branches[index]
    return {"index": index, "position": index + 1, "count": len(session.branches), "id": branch.id, "label": branch.label}


def status_view(session: Session) -> dict:
    return {
        "session_id": session.id,
        "turn_id": session.last_turn_id,
        "input_seq": session.tactile_input_seq,
        "tactile_input": session.tactile_input,
        "tactile_cue": session.tactile_cue,
        "branch_cursor": cursor_view(session),
    }


def demo_view(session: Session) -> dict:
    """Extra keys for the session view. Not part of WIR."""
    return {
        "tactile_cue": session.tactile_cue,
        "tactile_input": session.tactile_input,
        "branch_cursor": cursor_view(session),
        "tactile_demo_map": dict(DEMO_MAP),
    }


def handle_input(session_id: str, kind: str, level: int | None = None, origin: str = "on_screen") -> dict:
    from wayfinder import orchestrator as orch

    with runtime.lock:
        return apply_input(orch._session(session_id), kind, level, origin)


def apply_input(session: Session, kind: str, level: int | None = None, origin: str = "on_screen") -> dict:
    """Map one tactile input onto the same actions the keyboard and command path use."""
    from wayfinder import orchestrator as orch

    if kind not in INPUT_KINDS:
        raise TopologyError("invalid_payload")
    if kind == "analog":
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            raise TopologyError("invalid_payload")
    else:
        level = None
    session.tactile_input_seq += 1
    label = DEMO_MAP[kind]
    if origin == "on_screen":
        label = f"On-screen stand-in. {label}"
    record = {
        "seq": session.tactile_input_seq,
        "input": kind,
        "level": level,
        "origin": origin,
        "from_board": origin == "board_usb",
        "label": label,
        "at_ms": runtime.plane.now_ms(),
    }
    session.tactile_input = record

    if kind == "stop":
        session.branch_cursor = None
        return orch._turn(session, "stop", None, "touch")
    if kind == "back":
        return orch._turn(session, "go back", None, "touch")
    if kind == "confirm":
        if session.pending is not None:
            return orch._turn(session, "yes", None, "touch")
        cursor = cursor_view(session)
        if cursor is not None:
            return orch._turn(session, None, cursor["id"], "touch")
        return orch._turn(session, "yes", None, "touch")
    # next and analog only move the highlight. They never run a page action.
    count = len(session.branches)
    if count == 0:
        session.last_speech = "There are no options to move along."
        session.branch_cursor = None
        return orch._view(session)
    current = cursor_view(session)
    if kind == "next":
        target = 0 if current is None else (current["index"] + 1) % count
    else:
        target = min(count - 1, (level or 0) * count // 100)
        if current is not None and current["index"] == target:
            return orch._view(session)
    session.branch_cursor = {"turn_id": session.last_turn_id, "index": target}
    branch = session.branches[target]
    session.last_speech = f"Option {target + 1} of {count}: {branch.label}. Confirm to choose it."
    orch._emit(
        session,
        [],
        f"tactile-{session.tactile_input_seq}",
        session.last_turn_id or "tactile",
        "device",
        "branch_cursor",
        "ok",
        input=kind,
        origin=origin,
        position=target + 1,
        count=count,
    )
    return orch._view(session)
