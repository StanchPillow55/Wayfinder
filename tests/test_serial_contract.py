"""Phase 2 serial contract against an in-process loopback.

A loopback acknowledgement is not a typed ACK from a physical DevKit.
"""

import json
import os
import queue
import time

import pytest

from wayfinder.contracts import DEVKIT_ID, MAX_MESSAGE_BYTES
from wayfinder.models import DeviceMessage, TopologyError, parse_device_message
from wayfinder.orchestrator import ping_device
from wayfinder.state import get_runtime


class MemorySerial:
    """Bytes in, bytes out. This object is not a USB device."""

    def __init__(self, respond=None) -> None:
        self.respond = respond
        self.inbox = bytearray()
        self.out: queue.Queue[bytes] = queue.Queue()
        self.closed = False
        self.fail_reads = 0
        self.writes = 0

    def write(self, data: bytes) -> int:
        if self.closed:
            raise OSError("closed")
        self.writes += 1
        self.inbox.extend(data)
        while b"\n" in self.inbox:
            line, _, rest = self.inbox.partition(b"\n")
            self.inbox = bytearray(rest)
            if self.respond is None:
                continue
            reply = self.respond(bytes(line))
            if reply:
                self.out.put(reply if reply.endswith(b"\n") else reply + b"\n")
        return len(data)

    def readline(self) -> bytes:
        if self.closed:
            raise OSError("closed")
        if self.fail_reads:
            self.fail_reads -= 1
            raise OSError("disconnected")
        try:
            return self.out.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


def health_ack(line: bytes):
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if data.get("type") != "health":
        return None
    body = {
        "protocol_version": "0.1",
        "transport": "usb_serial",
        "trace_id": data["trace_id"],
        "message_id": "loop-ack",
        "sequence": data["sequence"],
        "source": "devkit_tactile",
        "destination": "mac_orchestrator",
        "type": "ack",
        "created_at_ms": 1,
        "payload": {
            "ack_message_id": data["message_id"],
            "status": "ok",
            "observed_angle_deg": 0,
            "state_token": "cleared",
            "pulse_us": 1000,
        },
        "requires_ack": False,
    }
    return json.dumps(body).encode()


def _wait(predicate) -> None:
    for _ in range(50):
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_port_override_binds_the_injected_opener():
    plane = get_runtime().plane
    opened: list[str] = []

    def opener(port: str) -> MemorySerial:
        opened.append(port)
        return MemorySerial()

    plane.port_opener = opener
    os.environ["WAYFINDER_DEVKIT_PORT"] = "loopback-devkit"
    try:
        plane.start_configured_links()
    finally:
        os.environ.pop("WAYFINDER_DEVKIT_PORT", None)
    assert opened == ["loopback-devkit"]
    board = plane.boards[DEVKIT_ID]
    assert board.simulated is False
    assert board.loopback is True
    assert board.status == "loopback"
    assert board.port == "loopback-devkit"


def test_loopback_health_is_not_a_physical_ack():
    plane = get_runtime().plane
    serial = MemorySerial(health_ack)
    plane.attach_serial(DEVKIT_ID, serial, port_name="loopback-devkit", loopback=True, backoff_s=(0, 0))
    health = ping_device(DEVKIT_ID)
    assert health["acknowledged"] is True
    assert health["simulated"] is False
    assert health["physical_servo_moved"] is False
    assert health["angle_deg"] == 0
    assert "Loopback" in health["detail"]
    assert "physical board" in health["detail"]
    assert serial.writes == 1


def test_malformed_json_protocol_version_and_size_do_not_move_the_link():
    plane = get_runtime().plane
    serial = MemorySerial(health_ack)
    seen: list[object] = []
    plane.on_event = seen.append
    plane.attach_serial(DEVKIT_ID, serial, port_name="loopback-devkit", loopback=True, backoff_s=(0, 0))
    link = plane.links[DEVKIT_ID]
    before = plane.boards[DEVKIT_ID].last_angle

    serial.out.put(b"{not-json\n")
    _wait(lambda: link.errors >= 1)

    serial.out.put(
        json.dumps(
            {
                "protocol_version": "0.2",
                "transport": "usb_serial",
                "trace_id": "bad",
                "message_id": "bad",
                "sequence": 1,
                "source": "devkit_tactile",
                "destination": "mac_orchestrator",
                "type": "health",
                "created_at_ms": 1,
                "payload": {},
                "requires_ack": False,
            }
        ).encode()
        + b"\n"
    )
    _wait(lambda: link.errors >= 2)

    serial.out.put(b"x" * (MAX_MESSAGE_BYTES + 8) + b"\n")
    _wait(lambda: link.errors >= 3)
    assert seen == []
    assert plane.boards[DEVKIT_ID].last_angle == before

    huge = DeviceMessage(
        transport="usb_serial",
        trace_id="big",
        message_id="big-1",
        sequence=9,
        source="mac_orchestrator",
        destination=DEVKIT_ID,
        type="health",
        created_at_ms=0,
        payload={"request": "z" * 1200},
        requires_ack=True,
    )
    refused = plane.dispatch(huge)
    assert refused.status == "message_too_large"
    assert refused.acknowledged is False
    assert serial.writes == 0

    with pytest.raises(TopologyError) as raised:
        parse_device_message(
            {
                "protocol_version": "9.9",
                "transport": "usb_serial",
                "trace_id": "bad",
                "message_id": "bad",
                "sequence": 1,
                "source": "mac_orchestrator",
                "destination": DEVKIT_ID,
                "type": "health",
                "created_at_ms": 0,
                "payload": {},
                "requires_ack": False,
            }
        )
    assert raised.value.code == "protocol_version"


def test_read_failure_reconnects_with_backoff_then_answers_health():
    plane = get_runtime().plane
    first = MemorySerial(health_ack)
    opened: list[MemorySerial] = []
    attempts = {"n": 0}

    def opener():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("down")
        fresh = MemorySerial(health_ack)
        opened.append(fresh)
        return fresh

    plane.attach_serial(
        DEVKIT_ID,
        first,
        port_name="loopback-devkit",
        opener=opener,
        loopback=True,
        backoff_s=(0, 0),
    )
    link = plane.links[DEVKIT_ID]
    first.fail_reads = 1
    first.out.put(b"\n")
    _wait(lambda: link.reconnects == 1 and not link.failed)
    assert len(opened) == 1
    health = ping_device(DEVKIT_ID)
    assert health["acknowledged"] is True
    assert health["physical_servo_moved"] is False
    assert "Loopback" in health["detail"]
    assert opened[0].writes == 1


# ---- bench scaffolding: truthful state, discovery, firmware source contract ----

from pathlib import Path

from wayfinder import devices
from wayfinder.contracts import BOX3_ID, P4_ID

ROOT = Path(__file__).resolve().parents[1]


def reply_with(source: str = "devkit_tactile", kind: str = "ack", payload_extra: dict | None = None):
    def respond(line: bytes):
        data = json.loads(line)
        payload = {"ack_message_id": data["message_id"], "status": "ok", "state_token": "cleared"}
        if kind == "error":
            payload = {"ack_message_id": data["message_id"], "code": "invalid_payload"}
        payload.update(payload_extra or {})
        return json.dumps(
            {
                "protocol_version": "0.1",
                "transport": "usb_serial",
                "trace_id": data["trace_id"],
                "message_id": "reply",
                "sequence": data["sequence"],
                "source": source,
                "destination": "mac_orchestrator",
                "type": kind,
                "created_at_ms": 1,
                "payload": payload,
                "requires_ack": False,
            }
        ).encode()

    return respond


def test_missing_port_is_an_error_not_a_simulator_or_usb(tmp_path):
    plane = get_runtime().plane
    os.environ["WAYFINDER_DEVKIT_PORT"] = str(tmp_path / "cu.not-there")
    try:
        plane.start_configured_links()
    finally:
        os.environ.pop("WAYFINDER_DEVKIT_PORT", None)
    board = plane.boards[DEVKIT_ID]
    assert board.status == "error"
    assert board.simulated is False
    assert board.connected is False
    assert "does not exist" in (board.error or "")
    row = next(item for item in plane.diagnostics() if item["id"] == DEVKIT_ID)
    assert row["physical_ack"] is False
    assert row["physical_motor_ran"] is False
    assert plane.ping(DEVKIT_ID).acknowledged is False


def test_unset_ports_stay_simulators_and_say_so():
    plane = get_runtime().plane
    plane.start_configured_links()
    for row in plane.diagnostics():
        assert row["status"] == "simulator"
        assert row["physical_ack"] is False
        assert row["physical_motor_ran"] is False
        assert "simulator" in str(row["state"])


def test_physical_ack_needs_a_real_link_and_the_right_board():
    plane = get_runtime().plane
    # A loopback is never a physical ack.
    plane.attach_serial(DEVKIT_ID, MemorySerial(reply_with()), port_name="lb", loopback=True, backoff_s=(0, 0))
    assert plane.ping(DEVKIT_ID).acknowledged is True
    assert plane.boards[DEVKIT_ID].physical_ack is False

    # A non-loopback link that answers as the right board is the only way to set it.
    plane.attach_serial(DEVKIT_ID, MemorySerial(reply_with()), port_name="usb-fake", backoff_s=(0, 0))
    assert plane.boards[DEVKIT_ID].status == "usb"
    assert plane.boards[DEVKIT_ID].physical_ack is False  # opened, no ack yet
    assert "No typed ack" in str(plane.diagnostics()[0]["state"])
    assert plane.ping(DEVKIT_ID).acknowledged is True
    assert plane.boards[DEVKIT_ID].physical_ack is True

    # A different board answering on the DevKit port is rejected.
    plane.attach_serial(DEVKIT_ID, MemorySerial(reply_with(BOX3_ID)), port_name="usb-fake", backoff_s=(0, 0))
    wrong = plane.ping(DEVKIT_ID)
    assert wrong.status == "wrong_board"
    assert wrong.acknowledged is False
    assert plane.boards[DEVKIT_ID].physical_ack is False


def test_firmware_error_reply_is_a_status_not_a_timeout():
    plane = get_runtime().plane
    plane.attach_serial(DEVKIT_ID, MemorySerial(reply_with(kind="error")), port_name="usb-fake", backoff_s=(0, 0))
    result = plane.ping(DEVKIT_ID)
    assert result.status == "invalid_payload"
    assert result.acknowledged is False


def test_motor_disabled_ack_is_not_a_motor_run():
    plane = get_runtime().plane
    respond = reply_with(payload_extra={"status": "motor_disabled", "motor_enabled_build": False})
    plane.attach_serial(DEVKIT_ID, MemorySerial(respond), port_name="usb-fake", backoff_s=(0, 0))
    from wayfinder.renderers import tactile_payload

    result = plane.dispatch(
        plane.command(DEVKIT_ID, "tactile.render", tactile_payload("orient", "t"), trace_id="md")
    )
    assert result.status == "motor_disabled"
    assert result.acknowledged is False
    assert result.fallback == "software"


def test_lockout_is_sent_as_lockout_and_released_only_on_a_real_link():
    plane = get_runtime().plane
    serial = MemorySerial(reply_with())
    plane.attach_serial(DEVKIT_ID, serial, port_name="usb-fake", backoff_s=(0, 0))
    plane.set_actuator(False)
    outbound = plane.boards[DEVKIT_ID].last_outbound
    assert outbound["type"] == "safe_disable"
    assert outbound["payload"]["lockout"] is True
    assert plane.entry(DEVKIT_ID)["status"] == "lockout"
    plane.set_actuator(True)
    released = plane.boards[DEVKIT_ID].last_outbound
    assert released["type"] == "safe_disable"
    assert released["payload"]["lockout"] is False
    assert plane.entry(DEVKIT_ID)["status"] == "usb"


def test_discovery_does_not_hardcode_or_assign_ports(monkeypatch):
    monkeypatch.setattr(
        devices.glob,
        "glob",
        lambda pattern: ["/dev/cu.usbmodem1101", "/dev/cu.Bluetooth-Incoming-Port"] if pattern == "/dev/cu.*" else [],
    )
    ports = devices.discover_serial_ports()
    assert [port.device for port in ports][0] == "/dev/cu.usbmodem1101"
    assert ports[0].likely_board is True
    assert next(port for port in ports if "Bluetooth" in port.device).likely_board is False
    assert set(devices.BOARD_PORT_ENV.values()) == {
        "WAYFINDER_DEVKIT_PORT",
        "WAYFINDER_BOX3_PORT",
        "WAYFINDER_P4_PORT",
    }
    for env in devices.BOARD_PORT_ENV.values():
        assert os.environ.get(env) is None  # discovery never sets them


def test_probe_sends_health_only_and_never_a_render(tmp_path, monkeypatch, capsys):
    port = tmp_path / "cu.usbmodem-test"
    port.write_text("")
    serial = MemorySerial(reply_with(P4_ID))
    lines: list[bytes] = []
    original = serial.write

    def spy(data: bytes) -> int:
        lines.append(data)
        return original(data)

    serial.write = spy  # type: ignore[method-assign]
    monkeypatch.setattr(devices, "_pyserial_opener", lambda _port: serial)
    code = devices.probe_board(P4_ID, str(port), attempts=2)
    assert code == 0
    assert "health ACK only" in capsys.readouterr().out
    assert lines
    for raw in lines:
        assert json.loads(raw)["type"] == "health"


def test_probe_reports_a_missing_port(capsys):
    assert devices.probe_board(DEVKIT_ID, "/dev/cu.definitely-missing") == 2
    assert "does not exist" in capsys.readouterr().out


def _source(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text()


def test_devkit_firmware_has_a_motorless_default_and_protocol_guards():
    ini = _source("firmware", "devkit_tactile_adapter", "platformio.ini")
    assert "default_envs = devkit_health_only" in ini
    assert "-DWAYFINDER_MOTOR_ENABLE=0" in ini
    assert "-DWAYFINDER_MOTOR_ENABLE=1" in ini
    assert "/dev/" not in ini
    source = _source("firmware", "devkit_tactile_adapter", "src", "main.cpp")
    for snippet in (
        "SOURCE ONLY",
        "duty_percent",
        "duration_ms",
        "direction",
        "safe_disable",
        "duplicate_sequence",
        "rate_limited",
        "lockout",
        "motor_disabled",
        "reverse_unsupported",
        "uart_topology_retired",
        "retired_topology",
        'strcmp(version, "0.1")',
        "message_too_large",
    ):
        assert snippet in source
    assert "servo" not in source.lower().replace("kservopin", "")


def test_box3_and_p4_scaffolds_do_not_pretend_to_be_flashed():
    for rel in (
        ("firmware", "esp32_s3_box3", "src", "main.cpp"),
        ("firmware", "p4_eye", "main", "main.c"),
    ):
        source = _source(*rel)
        assert "SOURCE ONLY" in source
        assert "uart_topology_retired" in source
        assert "esp32_c3_display" in source
        assert "transport_unsupported" in source
    box3 = _source("firmware", "esp32_s3_box3", "src", "main.cpp")
    for snippet in ("not_drawn", "not_spoken", "touch.event", "speech.event", "display.render", "speech.render"):
        assert snippet in box3
    p4 = _source("firmware", "p4_eye", "main", "main.c")
    assert "vision.event" in p4
    for banned in ("camera_fb", "esp_camera", "fb_get"):
        assert banned not in p4
    for rel in (("firmware", "esp32_s3_box3", "README.md"), ("firmware", "p4_eye", "README.md")):
        assert "source-only" in _source(*rel).lower()
    assert not (ROOT / "firmware" / "esp32_c3_display").exists()
