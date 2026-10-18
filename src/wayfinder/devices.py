"""USB device plane for DevKitC-1, BOX-3, and P4X-EYE.

Boards never address each other. UART to a C3 display is not a transport here.
See ADR-001.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

from wayfinder.contracts import (
    BOX3_ID,
    DEVKIT_ID,
    MAC_ID,
    MAX_ANGLE_DEG,
    MAX_MESSAGE_BYTES,
    MIN_MOVE_INTERVAL_MS,
    P4_ID,
    POSES,
    angle_to_pulse,
)
from wayfinder.models import DeviceMessage, TopologyError, parse_device_message


@dataclass
class Board:
    board_id: str
    hardware_name: str
    role: str
    link_kind: str
    port_env: str
    connected: bool = True
    simulated: bool = True
    actuator_enabled: bool = True
    status: str = "simulator"
    error: str | None = None
    sequence: int = 0
    last_move_ms: int = -100_000
    last_angle: int = 0
    pwm_attached: bool = False
    last_outbound: dict | None = None
    last_ack: dict | None = None
    display: dict | None = None
    speech_text: str | None = None
    applied_sequences: set[int] = field(default_factory=set)
    port: str | None = None
    loopback: bool = False


@dataclass
class DispatchResult:
    status: str
    acknowledged: bool
    simulated: bool
    fallback: str | None
    latency_ms: int
    ack: dict | None = None
    device_angle: int | None = None


class LinkError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class SerialLink:
    port_name: str
    board_id: str
    serial: object
    pending: dict[str, queue.Queue] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    on_event: object | None = None
    opener: object | None = None
    backoff_s: tuple[float, ...] = (0.2, 0.4, 0.8, 1.6)
    errors: int = 0
    reconnects: int = 0
    failed: bool = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name=f"wayfinder-{self.board_id}", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        try:
            self.serial.close()
        except Exception:
            pass

    def send(self, message: DeviceMessage, timeout: float = 0.5) -> dict | None:
        encoded = message.model_dump_json().encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise LinkError("message_too_large")
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self.lock:
            self.pending[message.message_id] = waiter
            handle = self.serial
        try:
            handle.write(encoded + b"\n")
            return waiter.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            with self.lock:
                self.pending.pop(message.message_id, None)

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                with self.lock:
                    handle = self.serial
                raw = handle.readline()
            except Exception:
                self.errors += 1
                if not self._reconnect():
                    break
                continue
            if not raw:
                continue
            if len(raw) > MAX_MESSAGE_BYTES + 1:
                self.errors += 1
                continue
            try:
                data = json.loads(raw.decode("utf-8"))
                message = parse_device_message(data)
            except Exception:
                self.errors += 1
                continue
            if message.type == "ack":
                ack_id = str(message.payload.get("ack_message_id", ""))
                with self.lock:
                    waiter = self.pending.get(ack_id)
                if waiter is not None:
                    waiter.put(message.model_dump(mode="json"))
                continue
            callback = self.on_event
            if callback is not None:
                try:
                    callback(message)
                except Exception:
                    self.errors += 1

    def _reconnect(self) -> bool:
        """Re-open the port with backoff. Failure leaves the link closed."""
        if self.opener is None:
            self.failed = True
            return False
        for delay in self.backoff_s:
            if self.stop.wait(delay):
                return False
            try:
                replacement = self.opener()
            except Exception:
                self.errors += 1
                continue
            with self.lock:
                try:
                    self.serial.close()
                except Exception:
                    pass
                self.serial = replacement
            self.reconnects += 1
            self.failed = False
            return True
        self.failed = True
        return False


def _pyserial_opener(port: str):
    import serial

    return serial.Serial(port, 115200, timeout=0.3)


class DevicePlane:
    """One Mac-owned link per board. Default links are simulators."""

    def __init__(self) -> None:
        self.boards = {
            DEVKIT_ID: Board(
                board_id=DEVKIT_ID,
                hardware_name="ESP32-S3-DevKitC-1",
                role="physical_output",
                link_kind="usb_serial",
                port_env="WAYFINDER_DEVKIT_PORT",
                actuator_enabled=True,
            ),
            BOX3_ID: Board(
                board_id=BOX3_ID,
                hardware_name="ESP32-S3-BOX-3",
                role="human_io",
                link_kind="usb_serial",
                port_env="WAYFINDER_BOX3_PORT",
            ),
            P4_ID: Board(
                board_id=P4_ID,
                hardware_name="P4X-EYE",
                role="perception",
                link_kind="usb_debug",
                port_env="WAYFINDER_P4_PORT",
            ),
        }
        self.frozen_ms: int | None = None
        self.dropped_events = 0
        self.links: dict[str, SerialLink] = {}
        self.on_event = None
        self._started = False
        self.port_opener = None

    def now_ms(self) -> int:
        if self.frozen_ms is not None:
            return self.frozen_ms
        return int(time.time() * 1000)

    def shutdown(self) -> None:
        for link in self.links.values():
            link.close()
        self.links.clear()
        self._started = False

    def start_configured_links(self) -> None:
        if self._started:
            return
        self._started = True
        for board in self.boards.values():
            port = os.environ.get(board.port_env) or None
            if not port:
                continue
            self._open_serial(board, port)

    def _open_serial(self, board: Board, port: str) -> None:
        opener = self.port_opener or _pyserial_opener
        try:
            handle = opener(port)
        except ImportError:
            board.connected = False
            board.simulated = False
            board.loopback = False
            board.status = "error"
            board.error = "pyserial is not installed"
            return
        except Exception as exc:
            board.connected = False
            board.simulated = False
            board.loopback = False
            board.status = "error"
            board.error = str(exc)
            board.port = port
            return
        self.attach_serial(
            board.board_id,
            handle,
            port_name=port,
            opener=lambda: opener(port),
            loopback=self.port_opener is not None,
        )

    def attach_serial(
        self,
        board_id: str,
        serial,
        *,
        port_name: str,
        opener=None,
        loopback: bool = False,
        backoff_s: tuple[float, ...] = (0.2, 0.4, 0.8, 1.6),
    ) -> Board:
        """Bind a serial object. A loopback is an in-process stand-in, not a physical board."""
        board = self._board(board_id)
        previous = self.links.pop(board_id, None)
        if previous is not None:
            previous.close()
        link = SerialLink(
            port_name=port_name,
            board_id=board.board_id,
            serial=serial,
            on_event=self._relay,
            opener=opener,
            backoff_s=backoff_s,
        )
        link.start()
        self.links[board.board_id] = link
        board.port = port_name
        board.connected = True
        board.simulated = False
        board.loopback = loopback
        board.status = "loopback" if loopback else "usb"
        board.error = None
        return board

    def _relay(self, message: DeviceMessage) -> None:
        if self.on_event is not None:
            self.on_event(message)

    def set_connection(self, board_id: str, status: str) -> Board:
        board = self._board(board_id)
        link = self.links.pop(board_id, None)
        if link is not None:
            link.close()
        if status == "disconnected":
            board.connected = False
            board.simulated = False
            board.loopback = False
            board.status = "disconnected"
            board.port = None
            return board
        if status == "simulator":
            board.connected = True
            board.simulated = True
            board.loopback = False
            board.status = "simulator"
            board.port = None
            board.error = None
            return board
        raise TopologyError("unsupported_connection")

    def set_actuator(self, enabled: bool) -> DispatchResult | None:
        board = self.boards[DEVKIT_ID]
        board.actuator_enabled = enabled
        if enabled:
            return None
        return self.dispatch(
            self.command(DEVKIT_ID, "safe_disable", {"state_token": "cleared", "detach": True}, trace_id="safe-disable")
        )

    def directory(self) -> dict[str, list[dict[str, str]]]:
        capture = [self.entry(BOX3_ID), self.entry(P4_ID)]
        render = [self.entry(DEVKIT_ID), self.entry(BOX3_ID), {
            "id": "software_tactile",
            "status": "available",
            "board": "mac",
            "role": "tactile_fallback",
            "link": "in_process",
        }]
        return {"capture": capture, "render": render}

    def channels(self) -> dict[str, list[str]]:
        inputs = ["text"]
        outputs = ["visual", "tactile"]
        if self.boards[BOX3_ID].connected:
            inputs.extend(["speech", "touch"])
            outputs.extend(["display", "speech"])
        if self.boards[P4_ID].connected:
            inputs.append("camera_event")
        return {"input": inputs, "output": outputs}

    def entry(self, board_id: str) -> dict[str, str]:
        board = self.boards[board_id]
        status = board.status
        if board_id == DEVKIT_ID and board.connected and not board.actuator_enabled:
            status = "lockout"
        return {
            "id": board.board_id,
            "status": status,
            "board": board.hardware_name,
            "role": board.role,
            "link": board.link_kind,
        }

    def ping(self, board_id: str) -> DispatchResult:
        """Health check. A successful ACK is not a motor run."""
        message = self.command(board_id, "health", {"request": "ping"}, trace_id="health")
        return self.dispatch(message)

    def next_sequence(self, board_id: str) -> int:
        board = self._board(board_id)
        board.sequence += 1
        return board.sequence

    def dispatch(self, message: DeviceMessage) -> DispatchResult:
        started = time.perf_counter()
        board = self._board(message.destination)
        board.last_outbound = message.model_dump(mode="json")
        if not board.connected:
            return self._result("disconnected", False, board, started, None)
        if message.destination == DEVKIT_ID and not board.actuator_enabled and message.type not in {"safe_disable", "health"}:
            return self._result("lockout", False, board, started, None)
        if board.simulated:
            ack = self._simulate(board, message)
            moved = ack["payload"]["status"] == "ok"
            return self._result("ok" if moved else ack["payload"]["status"], moved, board, started, ack)
        link = self.links.get(board.board_id)
        if link is None:
            return self._result("disconnected", False, board, started, None)
        try:
            raw = link.send(message)
        except LinkError as exc:
            return self._result(exc.code, False, board, started, None)
        if raw is None:
            return self._result("timeout", False, board, started, None)
        board.last_ack = raw
        payload = raw.get("payload", {})
        moved = payload.get("status") == "ok"
        if isinstance(payload.get("observed_angle_deg"), int):
            board.last_angle = payload["observed_angle_deg"]
        return self._result(str(payload.get("status", "ok")), moved, board, started, raw)

    def _simulate(self, board: Board, message: DeviceMessage) -> dict:
        status = "ok"
        if message.type in {"tactile.render", "safe_disable"}:
            detach = message.type == "safe_disable" or message.payload.get("detach") is True
            angle = 0 if detach else int(message.payload.get("angle_deg", board.last_angle))
            angle = max(0, min(MAX_ANGLE_DEG, angle))
            now = self.now_ms()
            if message.type == "tactile.render" and not detach:
                if message.sequence in board.applied_sequences:
                    status = "duplicate_sequence"
                    angle = board.last_angle
                elif now - board.last_move_ms < MIN_MOVE_INTERVAL_MS:
                    status = "rate_limited"
                    angle = board.last_angle
                else:
                    board.last_angle = angle
                    board.last_move_ms = now
                    board.pwm_attached = True
                    board.applied_sequences.add(message.sequence)
            else:
                board.last_angle = angle
                board.last_move_ms = now
                board.pwm_attached = not detach
        elif message.type == "display.render":
            board.display = dict(message.payload)
        elif message.type == "speech.render":
            board.speech_text = str(message.payload.get("text", ""))
        ack = self._ack(board, message, status)
        board.last_ack = ack
        return ack

    def _ack(self, board: Board, message: DeviceMessage, status: str) -> dict:
        observed = board.last_angle
        token = str(message.payload.get("state_token", "cleared"))
        body = {
            "protocol_version": "0.1",
            "transport": "simulator" if board.simulated else board.link_kind,
            "trace_id": message.trace_id,
            "message_id": uuid.uuid4().hex,
            "sequence": message.sequence,
            "source": board.board_id,
            "destination": MAC_ID,
            "type": "ack",
            "created_at_ms": self.now_ms(),
            "payload": {
                "ack_message_id": message.message_id,
                "status": status,
                "observed_angle_deg": observed,
                "state_token": token,
                "pulse_us": angle_to_pulse(observed) if token in POSES or True else angle_to_pulse(observed),
            },
            "requires_ack": False,
        }
        return body

    def command(
        self,
        destination: str,
        message_type: str,
        payload: dict[str, object],
        *,
        trace_id: str,
    ) -> DeviceMessage:
        board = self._board(destination)
        if board.simulated:
            transport = "simulator"
        elif board.link_kind == "usb_debug":
            transport = "usb_debug"
        else:
            transport = "usb_serial"
        return DeviceMessage(
            transport=transport,  # type: ignore[arg-type]
            trace_id=trace_id[:80],
            message_id=uuid.uuid4().hex,
            sequence=self.next_sequence(destination),
            source=MAC_ID,
            destination=destination,
            type=message_type,  # type: ignore[arg-type]
            created_at_ms=max(0, self.now_ms()),
            payload=payload,
            requires_ack=True,
        )

    def _result(
        self,
        status: str,
        acknowledged: bool,
        board: Board,
        started: float,
        ack: dict | None,
    ) -> DispatchResult:
        fallback = None if acknowledged else "software"
        return DispatchResult(
            status=status,
            acknowledged=acknowledged,
            simulated=board.simulated,
            fallback=fallback,
            latency_ms=int((time.perf_counter() - started) * 1000),
            ack=ack,
            device_angle=board.last_angle,
        )

    def _board(self, board_id: str) -> Board:
        try:
            return self.boards[board_id]
        except KeyError as exc:
            raise TopologyError("unknown_endpoint") from exc
