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
