"""USB device plane for DevKitC-1, BOX-3, and P4X-EYE.

Boards never address each other. UART to a C3 display is not a transport here.
See ADR-001.

Connection state is reported from what actually happened, not from what is
configured:

- ``simulator``: no port env var set. Nothing physical is involved.
- ``error``: an env var names a port that is missing or would not open.
- ``usb``: the port opened. That is not a health ACK.
- ``loopback``: an in-process test stand-in. It is never a board.
- ``Board.physical_ack`` becomes true only when a real serial link (not a
  loopback) returns a typed ack whose source is this board's id.

Nothing in this module energizes a motor. A health ping never sends
``tactile.render``. ``python -m wayfinder.devices --help`` lists the bench
helpers (port discovery, before/after USB diff, health-only probe).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import sys
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
    physical_ack: bool = False
    physical_ack_ms: int | None = None
    last_ack_status: str | None = None
    firmware: str | None = None
    boot_health: dict | None = None
    last_run: dict | None = None


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
            if message.type in {"ack", "error"}:
                ack_id = str(message.payload.get("ack_message_id", ""))
                with self.lock:
                    waiter = self.pending.get(ack_id)
                if waiter is not None:
                    waiter.put(message.model_dump(mode="json"))
                    continue
                if message.type == "ack":
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
        if self.port_opener is None and not os.path.exists(port):
            board.connected = False
            board.simulated = False
            board.loopback = False
            board.status = "error"
            board.port = port
            board.error = (
                f"{board.port_env} names {port}, which does not exist. "
                "Run python -m wayfinder.devices to list ports."
            )
            return
        try:
            handle = opener(port)
        except ImportError:
            board.connected = False
            board.simulated = False
            board.loopback = False
            board.status = "error"
            board.error = "pyserial is not installed. Run: pip install -e '.[serial]'"
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
        board.physical_ack = False
        board.physical_ack_ms = None
        board.last_ack_status = None
        board.firmware = None
        board.loopback = loopback
        board.status = "loopback" if loopback else "usb"
        board.error = None
        return board

    def _relay(self, message: DeviceMessage) -> None:
        board = self.boards.get(message.source)
        if board is not None and message.type == "health" and not board.loopback:
            board.boot_health = dict(message.payload)
            firmware = message.payload.get("firmware")
            board.firmware = firmware if isinstance(firmware, str) else None
        if self.on_event is not None:
            self.on_event(message)

    def set_connection(self, board_id: str, status: str) -> Board:
        board = self._board(board_id)
        link = self.links.pop(board_id, None)
        if link is not None:
            link.close()
        board.physical_ack = False
        board.physical_ack_ms = None
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
            # Only a real link holds a firmware lockout latch to release.
            # Simulator and disconnected boards have nothing to unlatch.
            if board.connected and not board.simulated and DEVKIT_ID in self.links:
                return self.dispatch(
                    self.command(
                        DEVKIT_ID,
                        "safe_disable",
                        {"state_token": "cleared", "detach": True, "lockout": False},
                        trace_id="safe-enable",
                    )
                )
            return None
        # Lockout is a Mac status that firmware also latches. It is not the same
        # as a detach, which the next render may undo.
        return self.dispatch(
            self.command(
                DEVKIT_ID,
                "safe_disable",
                {"state_token": "cleared", "detach": True, "lockout": True},
                trace_id="safe-disable",
            )
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
        if raw.get("source") != board.board_id:
            # Something else answered on this port. It is not this board.
            board.last_ack_status = "wrong_board"
            return self._result("wrong_board", False, board, started, raw)
        if raw.get("type") == "error":
            code = str(payload.get("code", "error"))
            board.last_ack_status = code
            return self._result(code, False, board, started, raw)
        status = str(payload.get("status", "ok"))
        board.last_ack_status = status
        if not board.loopback:
            board.physical_ack = True
            board.physical_ack_ms = self.now_ms()
        moved = status == "ok"
        if isinstance(payload.get("observed_angle_deg"), int):
            board.last_angle = payload["observed_angle_deg"]
        return self._result(status, moved, board, started, raw)

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
                    board.last_run = {
                        "duty_percent": message.payload.get("duty_percent"),
                        "duration_ms": message.payload.get("duration_ms"),
                        "direction": message.payload.get("direction"),
                    }
            else:
                board.last_angle = angle
                board.last_move_ms = now
                board.pwm_attached = not detach
                board.last_run = None
        elif message.type == "display.render":
            board.display = dict(message.payload)
        elif message.type == "speech.render":
            board.speech_text = str(message.payload.get("text", ""))
        ack = self._ack(board, message, status)
        board.last_ack = ack
        return ack

    def _ack(self, board: Board, message: DeviceMessage, status: str) -> dict:
        # observed_angle_deg and pulse_us echo the commanded pulse hint. The
        # motor does not hold an angle and nothing here observes it.
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
                "pulse_us": angle_to_pulse(observed),
                **(board.last_run or {}),
                **({"output_driven": False} if board.simulated else {}),
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

    def diagnostics(self) -> list[dict[str, object]]:
        """Truthful per-board connection state for the bench. Not a UI string.

        ``physical_motor_ran`` is always False here: no code path in this module
        can observe a motor, and a health ACK is not a motor run.
        """
        rows: list[dict[str, object]] = []
        for board in self.boards.values():
            rows.append(
                {
                    "id": board.board_id,
                    "hardware": board.hardware_name,
                    "env_var": board.port_env,
                    "port": board.port,
                    "status": board.status,
                    "state": _connection_state(board),
                    "physical_ack": board.physical_ack,
                    "last_ack_status": board.last_ack_status,
                    "firmware": board.firmware,
                    "error": board.error,
                    "physical_motor_ran": False,
                }
            )
        return rows

    def _board(self, board_id: str) -> Board:
        try:
            return self.boards[board_id]
        except KeyError as exc:
            raise TopologyError("unknown_endpoint") from exc


def _connection_state(board: Board) -> str:
    if board.status == "error":
        return f"error: {board.error}"
    if board.status == "disconnected":
        return "disconnected by the operator"
    if board.loopback:
        return "loopback stand-in. No board is attached."
    if board.simulated:
        return f"simulator. {board.port_env} is not set, so nothing physical is involved."
    if board.physical_ack:
        return f"typed ack received from {board.board_id} on {board.port}. Not a motor run."
    return f"port {board.port} opened. No typed ack from {board.board_id} yet."


# ---- bench helpers: port discovery, USB diff, health-only probe ----------------

BOARD_PORT_ENV = {
    DEVKIT_ID: "WAYFINDER_DEVKIT_PORT",
    BOX3_ID: "WAYFINDER_BOX3_PORT",
    P4_ID: "WAYFINDER_P4_PORT",
}

# USB vendor ids are public. They hint at a bridge chip or native USB, never at
# which board sits behind it.
_VENDOR_HINTS = {
    0x303A: "Espressif native USB (ESP32-S3 or P4 candidates; cannot tell which board)",
    0x10C4: "Silicon Labs CP210x UART bridge",
    0x1A86: "WCH CH34x UART bridge",
    0x0403: "FTDI UART bridge",
}


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    source: str = "glob"
    likely_board: bool = False
    hint: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "description": self.description,
            "vid": f"{self.vid:04x}" if self.vid is not None else None,
            "pid": f"{self.pid:04x}" if self.pid is not None else None,
            "source": self.source,
            "likely_board": self.likely_board,
            "hint": self.hint,
        }


def _looks_like_usb_serial(name: str) -> bool:
    lowered = os.path.basename(name).lower()
    return any(token in lowered for token in ("usb", "slab", "acm"))


def discover_serial_ports() -> list[PortInfo]:
    """List serial ports without choosing one. Uses pyserial when installed, else /dev globs."""
    found: dict[str, PortInfo] = {}
    try:
        from serial.tools import list_ports

        for entry in list_ports.comports():
            vid = getattr(entry, "vid", None)
            hint = _VENDOR_HINTS.get(vid, "") if vid is not None else ""
            found[entry.device] = PortInfo(
                device=entry.device,
                description=str(getattr(entry, "description", "") or ""),
                vid=vid,
                pid=getattr(entry, "pid", None),
                source="pyserial",
                likely_board=bool(hint) or _looks_like_usb_serial(entry.device),
                hint=hint,
            )
    except ImportError:
        pass
    for pattern in ("/dev/cu.*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        for device in sorted(glob.glob(pattern)):
            if device not in found:
                found[device] = PortInfo(
                    device=device,
                    source="glob",
                    likely_board=_looks_like_usb_serial(device),
                )
    return sorted(found.values(), key=lambda port: (not port.likely_board, port.device))


def port_environment() -> dict[str, str | None]:
    return {env: os.environ.get(env) or None for env in BOARD_PORT_ENV.values()}


def _print_ports(ports: list[PortInfo], out) -> None:
    likely = [port for port in ports if port.likely_board]
    other = [port for port in ports if not port.likely_board]
    if not ports:
        print("No serial ports found. Check that the cable carries data and the board is powered.", file=out)
    if likely:
        print("Candidate USB serial ports:", file=out)
        for port in likely:
            extra = f"  [{port.hint}]" if port.hint else ""
            ids = f"  vid:pid={port.vid:04x}:{port.pid:04x}" if port.vid is not None and port.pid is not None else ""
            print(f"  {port.device}{ids}{extra}", file=out)
    if other:
        print("Other serial devices (probably not a board):", file=out)
        for port in other:
            print(f"  {port.device}", file=out)


def _print_environment(out) -> None:
    print("Port environment variables (unset means that board runs as a simulator):", file=out)
    for board_id, env in BOARD_PORT_ENV.items():
        value = os.environ.get(env)
        print(f"  {env}={value if value else '(unset)'}  -> {board_id}", file=out)


def probe_board(board_id: str, port: str, *, attempts: int = 4, out=None) -> int:
    """Send health pings only. Never sends tactile.render, so no motor command is possible."""
    out = out or sys.stdout
    if board_id not in BOARD_PORT_ENV:
        print(f"Unknown board id {board_id}. Use one of: {', '.join(BOARD_PORT_ENV)}", file=out)
        return 2
    plane = DevicePlane()
    board = plane.boards[board_id]
    plane.on_event = lambda message: None
    plane._open_serial(board, port)
    if board.status == "error":
        print(f"Cannot open {port}: {board.error}", file=out)
        return 2
    code = 1
    try:
        for attempt in range(1, attempts + 1):
            result = plane.ping(board_id)
            if result.acknowledged and board.physical_ack:
                print(
                    f"Typed health ACK from {board_id} on {port} (attempt {attempt}). "
                    "This is a health ACK only. The motor has not been commanded.",
                    file=out,
                )
                if board.firmware:
                    print(f"Firmware reported: {board.firmware}", file=out)
                code = 0
                break
            print(f"Attempt {attempt}: no ACK ({result.status}).", file=out)
            if result.status == "wrong_board":
                print(f"The port answered as a different board. Not {board_id}.", file=out)
                code = 3
                break
            # Opening the port can reset an ESP32; give it time to boot.
            time.sleep(0.8)
        else:
            print(
                f"No typed ACK from {board_id} on {port}. If nothing is flashed, "
                "this is expected. Do not treat the port as that board.",
                file=out,
            )
    finally:
        plane.shutdown()
    return code


def _diff_ports(out, wait=input) -> int:
    before = {port.device: port for port in discover_serial_ports()}
    print("Baseline with the boards UNPLUGGED:", file=out)
    _print_ports(list(before.values()), out)
    wait("\nPlug in ONE board by its data cable, wait 3 seconds, then press Enter: ")
    after = {port.device: port for port in discover_serial_ports()}
    added = [port for device, port in after.items() if device not in before]
    gone = [device for device in before if device not in after]
    if added:
        print("New ports:", file=out)
        _print_ports(added, out)
        print(
            "\nWrite these down. They are not assigned to a board until a health ACK arrives:\n"
            "  python -m wayfinder.devices --probe <board_id> --port <path>",
            file=out,
        )
        return 0
    if gone:
        print(f"Ports disappeared: {', '.join(gone)}", file=out)
    print(
        "No new port appeared. Try a different cable (many are charge-only), another socket, "
        "or hold BOOT while plugging in.",
        file=out,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wayfinder.devices",
        description="Bench helpers. Lists ports, diffs USB before/after plugging, and sends health pings only.",
    )
    parser.add_argument("--json", action="store_true", help="print ports and env state as JSON")
    parser.add_argument("--diff", action="store_true", help="interactive before/after port diff")
    parser.add_argument("--probe", metavar="BOARD_ID", help=f"health-only ping: {', '.join(BOARD_PORT_ENV)}")
    parser.add_argument("--port", help="port for --probe. Defaults to that board's env var")
    args = parser.parse_args(argv)

    if args.probe:
        port = args.port or os.environ.get(BOARD_PORT_ENV.get(args.probe, ""), "")
        if not port:
            env = BOARD_PORT_ENV.get(args.probe, "<env var>")
            print(f"No port. Pass --port or set {env}.")
            return 2
        return probe_board(args.probe, port)
    if args.diff:
        return _diff_ports(sys.stdout)
    ports = discover_serial_ports()
    if args.json:
        print(json.dumps({"ports": [port.as_dict() for port in ports], "env": port_environment()}, indent=2))
        return 0
    _print_ports(ports, sys.stdout)
    print()
    _print_environment(sys.stdout)
    print("\nNothing here assigns a port to a board. Set the env var yourself after a health ACK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
