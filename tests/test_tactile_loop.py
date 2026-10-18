"""Tactile loop: Braille word cues out, DevKit buttons and analog in.

Every serial line here is a fake in-process line. No port is opened and no board
is involved, so nothing in this file says a motor spun.
"""

import json
import queue
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wayfinder.api import app
from wayfinder.contracts import BOX3_ID, DEVKIT_ID, MAX_MESSAGE_BYTES, MOTOR_RUNS
from wayfinder.models import TopologyError, parse_device_message
from wayfinder.orchestrator import create_session, ingest_message, on_board_event, ping_device, set_connection
from wayfinder.state import get_runtime
from wayfinder.tactile_loop import CUE_TOKEN, apply_input, cue_duration_ms, cursor_view, publish_word_cue

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"


class FakeSerial:
    """A fake serial line. feed() is what a board would print. write() is what the Mac sent."""

    def __init__(self, ack_status: str | None = None) -> None:
        self.ack_status = ack_status
        self.inbox = bytearray()
        self.out: queue.Queue[bytes] = queue.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def feed(self, payload: dict, *, message_type: str = "touch.event", source: str = DEVKIT_ID) -> None:
        line = {
            "protocol_version": "0.1",
            "transport": "usb_serial",
            "trace_id": "tactile-input",
            "message_id": f"dk{time.perf_counter_ns()}",
            "sequence": 0,
            "source": source,
            "destination": "mac_orchestrator",
            "type": message_type,
            "created_at_ms": 5,
            "payload": payload,
            "requires_ack": False,
        }
        encoded = json.dumps(line).encode()
        assert len(encoded) < MAX_MESSAGE_BYTES
        self.out.put(encoded + b"\n")

    def write(self, data: bytes) -> int:
        if self.closed:
            raise OSError("closed")
        self.inbox.extend(data)
        while b"\n" in self.inbox:
            raw, _, rest = self.inbox.partition(b"\n")
            self.inbox = bytearray(rest)
            message = json.loads(raw)
            self.sent.append(message)
            status = "ok" if message["type"] == "health" else (self.ack_status or "motor_disabled")
            self.out.put(
                json.dumps(
                    {
                        "protocol_version": "0.1",
                        "transport": "usb_serial",
                        "trace_id": message["trace_id"],
                        "message_id": f"ack{len(self.sent)}",
                        "sequence": message["sequence"],
                        "source": DEVKIT_ID,
                        "destination": "mac_orchestrator",
                        "type": "ack",
                        "created_at_ms": 1,
                        "payload": {
                            "ack_message_id": message["message_id"],
                            "status": status,
                            "state_token": message["payload"].get("state_token", "cleared"),
                        },
                        "requires_ack": False,
                    }
                ).encode()
                + b"\n"
            )
        return len(data)

    def readline(self) -> bytes:
        if self.closed:
            raise OSError("closed")
        try:
            return self.out.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


def _wait(predicate) -> None:
    for _ in range(100):
        if predicate():
            # The board callback holds the runtime lock while it applies the input.
            with get_runtime().lock:
                return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _session(view):
    return get_runtime().sessions[view["session_id"]]


# ---- word cue out ---------------------------------------------------------------


def test_cue_is_bounded_orient_forward_and_says_the_motor_has_not_spun_in_the_simulator():
    view = create_session()
    assert view["braille"]["words"]
    cue = publish_word_cue(view["session_id"], "Harbor", 0, 4)
    run = MOTOR_RUNS[CUE_TOKEN]
    assert cue["state_token"] == "orient" == CUE_TOKEN
    assert cue["direction"] == "forward"
    assert 0 < cue["duty_percent"] <= run["duty_percent"]
    assert 0 < cue["duration_ms"] <= run["duration_ms"]
    assert cue["word"] == "Harbor"
    assert cue["simulated"] is True
    assert cue["physical_motor_ran"] is False
    assert "motor has not spun" in cue["note"]
    assert cue_duration_ms("a") < cue_duration_ms("Harbor") <= run["duration_ms"]
    again = get_runtime().sessions[view["session_id"]].tactile_cue
    assert again == cue


def test_cue_with_no_board_connected_still_describes_itself_and_never_claims_a_run():
    view = create_session()
    set_connection(DEVKIT_ID, "disconnected")
    cue = publish_word_cue(view["session_id"], "Options", 1, 3)
    assert cue["dispatch_status"] == "disconnected"
    assert cue["acknowledged"] is False
    assert cue["physical_motor_ran"] is False
    assert cue["duty_percent"] > 0 and cue["duration_ms"] > 0
    assert "motor has not spun" in cue["note"]
    assert "No DevKit is connected" in cue["note"]


@pytest.mark.parametrize(
    "ack_status, loopback, expected_ran",
    [
        ("motor_disabled", False, False),  # health-only build: protocol ok, pin untouched
        ("ok", True, False),  # a loopback is never a board
        ("rate_limited", False, False),
        ("ok", False, True),  # only a real link with an ok render ack
    ],
)
def test_physical_motor_ran_needs_a_real_board_ack_for_the_render(ack_status, loopback, expected_ran):
    view = create_session()
    plane = get_runtime().plane
    line = FakeSerial(ack_status=ack_status)
    plane.attach_serial(DEVKIT_ID, line, port_name="fake-usb", loopback=loopback, backoff_s=(0, 0))
    cue = publish_word_cue(view["session_id"], "Harbor", 0, 2)
    renders = [m for m in line.sent if m["type"] == "tactile.render"]
    assert len(renders) == 1
    sent = renders[0]
    assert sent["destination"] == DEVKIT_ID
    assert sent["payload"]["state_token"] == "orient"
    assert sent["payload"]["direction"] == "forward"
    assert sent["payload"]["duty_percent"] == cue["duty_percent"]
    assert sent["payload"]["duration_ms"] == cue["duration_ms"]
    assert cue["board_ack_status"] == ack_status
    assert cue["physical_motor_ran"] is expected_ran
    if expected_ran:
        assert "does not prove the motor rotated" in cue["note"]
    else:
        assert "motor has not spun" in cue["note"]


def test_a_health_ping_is_not_a_motor_run():
    create_session()
    plane = get_runtime().plane
    line = FakeSerial(ack_status="ok")
    plane.attach_serial(DEVKIT_ID, line, port_name="fake-usb", backoff_s=(0, 0))
    result = ping_device(DEVKIT_ID)
    assert result["acknowledged"] is True
    assert result["physical_motor_ran"] is False
    assert all(message["type"] == "health" for message in line.sent)


@pytest.mark.parametrize("word", ["", "   ", "x" * 41, "bad\x00word", 12])
def test_cue_refuses_a_bad_word(word):
    view = create_session()
    with pytest.raises(TopologyError):
        publish_word_cue(view["session_id"], word)


def test_braille_view_names_the_words_the_plate_forms():
    view = create_session()
    ranges = 0
    start = None
    for index, cell in enumerate(view["braille"]["cells"] + [0]):
        if cell and start is None:
            start = index
        if not cell and start is not None:
            ranges += 1
            start = None
    if not view["braille"]["truncated"]:
        assert len(view["braille"]["words"]) == ranges


# ---- touch.event contract -------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"demo_input": "next"},
        {"demo_input": "back"},
        {"demo_input": "analog", "level": 0},
        {"demo_input": "analog", "level": 100},
        {"control": "confirm"},
        {"control": "stop"},
        {"selection": 3},
    ],
)
def test_touch_event_accepts_the_demo_shapes_and_the_old_ones(payload):
    message = parse_device_message(
        {
            "protocol_version": "0.1",
            "transport": "usb_serial",
            "trace_id": "t",
            "message_id": "m",
            "sequence": 1,
            "source": DEVKIT_ID,
            "destination": "mac_orchestrator",
            "type": "touch.event",
            "created_at_ms": 1,
            "payload": payload,
            "requires_ack": False,
        }
    )
    assert message.type == "touch.event"


@pytest.mark.parametrize(
    "payload",
    [
        {"demo_input": "confirm"},
        {"demo_input": "analog"},
        {"demo_input": "analog", "level": 101},
        {"demo_input": "analog", "level": -1},
        {"demo_input": "analog", "level": True},
        {"demo_input": "next", "level": 5},
        {"demo_input": "next", "selection": 2},
        {"selection": 9},
        {"control": "next"},
    ],
)
def test_touch_event_still_rejects_everything_else(payload):
    with pytest.raises(TopologyError):
        parse_device_message(
            {
                "protocol_version": "0.1",
                "transport": "usb_serial",
                "trace_id": "t",
                "message_id": "m",
                "sequence": 1,
                "source": DEVKIT_ID,
                "destination": "mac_orchestrator",
                "type": "touch.event",
                "created_at_ms": 1,
                "payload": payload,
                "requires_ack": False,
            }
        )


def test_only_the_devkit_may_send_demo_input():
    view = create_session()
    body = {
        "protocol_version": "0.1",
        "transport": "simulator",
        "trace_id": "t",
        "message_id": "m",
        "sequence": 1,
        "source": BOX3_ID,
        "destination": "mac_orchestrator",
        "type": "touch.event",
        "created_at_ms": 1,
        "payload": {"demo_input": "next"},
        "requires_ack": False,
    }
    with pytest.raises(TopologyError):
        ingest_message(view["session_id"], body)


# ---- buttons and analog in, over a fake serial line -----------------------------


def test_fake_serial_buttons_and_analog_navigate_the_session():
    view = create_session()
    session = _session(view)
    count = len(view["branches"])
    assert count >= 3
    plane = get_runtime().plane
    plane.on_event = on_board_event  # other tests may leave their own callback behind
    line = FakeSerial()
    plane.attach_serial(DEVKIT_ID, line, port_name="fake-usb", backoff_s=(0, 0))

    # Button A: next branch moves the highlight and runs nothing.
    turn_before = session.last_turn_id
    line.feed({"demo_input": "next"})
    _wait(lambda: session.tactile_input_seq == 1)
    assert session.last_turn_id == turn_before
    assert session.branch_cursor["index"] == 0
    assert session.tactile_input["input"] == "next"
    assert session.tactile_input["origin"] == "board_usb"
    assert session.tactile_input["from_board"] is True
    assert "Option 1" in session.last_speech

    line.feed({"demo_input": "next"})
    _wait(lambda: session.tactile_input_seq == 2)
    assert session.branch_cursor["index"] == 1

    # Analog: low to high walks the same list. Page is unchanged.
    line.feed({"demo_input": "analog", "level": 100})
    _wait(lambda: session.tactile_input_seq == 3)
    assert session.branch_cursor["index"] == count - 1
    line.feed({"demo_input": "analog", "level": 0})
    _wait(lambda: session.tactile_input_seq == 4)
    assert session.branch_cursor["index"] == 0
    assert session.last_turn_id == turn_before

    # Button B: confirm picks the highlighted branch, the same as pressing its number.
    chosen = session.branches[0]
    line.feed({"control": "confirm"})
    _wait(lambda: session.tactile_input_seq == 5)
    assert session.last_turn_id != turn_before
    assert any(event["detail"].get("channel") == "touch" for event in session.last_events)
    assert any(chosen.id in str(event["detail"]) for event in session.last_events)
    assert cursor_view(session) is None  # a new turn re-ranks the branches, so the old highlight is gone

    # Button C short: back. Button C held: stop.
    line.feed({"demo_input": "back"})
    _wait(lambda: session.tactile_input_seq == 6)
    assert session.tactile_input["input"] == "back"
    line.feed({"control": "stop"})
    _wait(lambda: session.tactile_input_seq == 7)
    assert session.tactile_input["input"] == "stop"
    assert session.branch_cursor is None


def test_confirm_with_a_pending_confirmation_says_yes():
    view = create_session()
    session = _session(view)
    # Walk to a branch that needs confirmation, if the fixture has one. Otherwise yes is a no-op.
    for index, branch in enumerate(session.branches):
        if branch.risk_class in {"consequential", "irreversible"}:
            apply_input(session, "analog", (index * 100) // len(session.branches) + 1, "on_screen")
            apply_input(session, "confirm", None, "on_screen")
            assert session.pending is not None
            reply = apply_input(session, "confirm", None, "on_screen")
            assert session.pending is None
            assert reply["pending_confirmation"] is None
            return
    reply = apply_input(session, "confirm", None, "on_screen")
    assert "Nothing is waiting" in reply["speech"]


def test_on_screen_stand_ins_use_the_same_actions_and_say_so():
    view = create_session()
    session = _session(view)
    reply = apply_input(session, "next", None, "on_screen")
    assert reply["branch_cursor"]["position"] == 1
    assert reply["tactile_input"]["origin"] == "on_screen"
    assert reply["tactile_input"]["from_board"] is False
    assert reply["tactile_input"]["label"].startswith("On-screen stand-in")
    with pytest.raises(TopologyError):
        apply_input(session, "analog", 101, "on_screen")
    with pytest.raises(TopologyError):
        apply_input(session, "sideways", None, "on_screen")


def test_simulated_devkit_message_is_not_labelled_a_board():
    view = create_session()
    ingest_message(
        view["session_id"],
        {
            "protocol_version": "0.1",
            "transport": "simulator",
            "trace_id": "t",
            "message_id": "m",
            "sequence": 1,
            "source": DEVKIT_ID,
            "destination": "mac_orchestrator",
            "type": "touch.event",
            "created_at_ms": 1,
            "payload": {"demo_input": "next"},
            "requires_ack": False,
        },
    )
    session = _session(view)
    assert session.tactile_input["origin"] == "simulated"
    assert session.tactile_input["from_board"] is False


# ---- HTTP and static assets -----------------------------------------------------


def test_http_cue_input_and_status():
    with TestClient(app) as client:
        opened = client.post("/api/sessions").json()
        sid = opened["session_id"]
        cue = client.post(f"/api/sessions/{sid}/tactile/cue", json={"word": "Harbor", "index": 0, "count": 3})
        assert cue.status_code == 200
        body = cue.json()
        assert body["state_token"] == "orient" and body["physical_motor_ran"] is False
        assert client.post(f"/api/sessions/{sid}/tactile/cue", json={"word": ""}).status_code == 422
        moved = client.post(f"/api/sessions/{sid}/tactile/input", json={"input": "next"}).json()
        assert moved["branch_cursor"]["position"] == 1
        assert moved["tactile_input"]["origin"] == "on_screen"
        assert moved["tactile_cue"]["word"] == "Harbor"
        assert client.post(f"/api/sessions/{sid}/tactile/input", json={"input": "analog", "level": 150}).status_code == 422
        status = client.get(f"/api/sessions/{sid}/tactile").json()
        assert status["input_seq"] == 1 and status["branch_cursor"]["position"] == 1
        assert client.get("/api/sessions/nope/tactile").status_code == 404
        assert client.get("/static/tactile.js").status_code == 200


def test_web_panel_keeps_braille_and_command_and_labels_stand_ins():
    page = (WEB / "index.html").read_text()
    tactile = (WEB / "tactile.js").read_text()
    braille = (WEB / "braille.js").read_text()
    app_js = (WEB / "app.js").read_text()
    assert page.index("/static/braille.js") < page.index("/static/tactile.js") < page.index("/static/app.js")
    assert 'id="command-form"' in page and 'id="braille-plate"' in page
    for node in ("tactile-cue-text", "tactile-input-text", "tactile-cursor-text", "tactile-analog"):
        assert f'id="{node}"' in page
    assert page.count("stand-in") >= 5
    assert "not a physical Braille device" in page
    assert "does not prove the motor rotated" in page
    assert "wayfinder:braille-word" in braille and "wayfinder:braille-word" in tactile
    assert "animateWordsForming" in braille and "animateWindowStagger" in braille
    assert "WayfinderTactile.render(state)" in app_js
    assert "motor has not spun" in tactile
    # The tactile panel comes after the command field so the first focusable controls do not move.
    assert page.index('id="command-form"') < page.index('id="tactile-loop"')


# ---- firmware source contract (no compile, no board) ----------------------------


def _firmware() -> str:
    return (ROOT / "firmware" / "devkit_tactile_adapter" / "src" / "main.cpp").read_text()


def test_firmware_inputs_emit_only_shapes_the_mac_accepts():
    source = _firmware()
    sent = set(re.findall(r'sendTouchInput\("(\w+)", "(\w+)"', source))
    assert sent == {
        ("demo_input", "next"),
        ("control", "confirm"),
        ("demo_input", "back"),
        ("control", "stop"),
        ("demo_input", "analog"),
    }
    assert "touch.event" in source
    assert "UNVERIFIED" in source and "NOT A CLINICAL DEVICE" in source
    assert "protocol 0.1" in source.lower()


def test_firmware_inputs_do_not_touch_the_motor_pin_and_health_build_stays_safe():
    source = _firmware()
    body = source[source.index("static void setupInputs()"):source.index("static void onButtonPressed")]
    assert "kMotorSignalPin" not in body and "kServoPin" not in body
    assert "pinMode(b.pin, INPUT_PULLUP)" in body
    assert "OUTPUT" not in source
    # Motor pin configuration stays behind the motor-enable guard.
    guarded = source.split("#if WAYFINDER_MOTOR_ENABLE")
    assert len(guarded) >= 3
    for chunk in (source[: source.index("static void writePulse")],):
        assert "ledcAttach" not in chunk
    # The installed Arduino-ESP32 core (2.x) uses the channel-based LEDC API, so
    # the single motor-pin attach site is ledcAttachPin(pin, channel), not the
    # 3.x pin-based ledcAttach(pin, freq, res). Still exactly one attach site.
    assert source.count("ledcAttachPin(kMotorSignalPin") == 1
    assert "pollInputs();" in source and "setupInputs();" in source
    assert "servo" not in source.lower().replace("kservopin", "")
    assert "kServoPin = 18" in source
