# DevKitC-1 tactile adapter

**Source-only.** This firmware has not been flashed to a board and has only been syntax-checked on a host with stubbed Arduino headers. No physical ACK from it exists.

It is the physical-output side of [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md). The Mac sends `tactile.render`, `safe_disable`, and `health`. The DevKit answers with `ack` or `error` on the same USB serial port. It does not join a UART bus, and it does not accept `esp32_c3_display` as a peer.

## Two builds

| Env | `WAYFINDER_MOTOR_ENABLE` | What it does |
| --- | --- | --- |
| `devkit_health_only` (default) | 0 | Never configures the signal pin. `tactile.render` runs every protocol check, then answers `motor_disabled`. Safe with nothing wired. |
| `devkit_motor_bench` | 1 | Drives the signal pin. Flash it only after the meter checks in the sprint runbook and a typed health ACK from the first build. |

Always flash `devkit_health_only` first.

## Flash

Board: ESP32-S3-DevKitC-1. Framework: Arduino-ESP32 3.x (`ledcAttach` / `ledcWrite` / `ledcDetach`). Install PlatformIO (`pipx install platformio` or `pip install platformio`).

USB CDC on boot is disabled so the NDJSON stream uses the UART bridge:

- DevKitC-1 with one Micro-USB socket: use the USB-C-to-Micro-USB data cable.
- DevKitC-1 v1.1 with two USB-C sockets: use a USB-C data cable in the socket marked UART.

Which socket this kit has is unverified until the board is in hand.

```bash
python -m wayfinder.devices --diff        # find the new port, do not guess
export WAYFINDER_DEVKIT_PORT=<the path it printed>
cd firmware/devkit_tactile_adapter
pio run -e devkit_health_only -t upload --upload-port "$WAYFINDER_DEVKIT_PORT"
pio device monitor -p "$WAYFINDER_DEVKIT_PORT"   # expect one boot "health" line
python -m wayfinder.devices --probe devkit_tactile
```

If the upload cannot connect, hold BOOT, tap RESET, release BOOT, and retry. The bridge chip name (`usbserial`, `wchusbserial`, `SLAB_USBtoUART`, `usbmodem`) is unverified.

## Protocol behavior

`tactile.render` uses `duty_percent`, `duration_ms`, and `direction` when present, and the frozen pose table (mirrors `MOTOR_RUNS`) when absent. `angle_deg` and `pulse_us` are range-checked and echoed as an unverified ESC-style hint. They do not choose the run.

| Case | Ack status | Output |
| --- | --- | --- |
| `health` | `ok` | Untouched. Not a motor run. |
| Same `sequence` as the last accepted render | `duplicate_sequence` | Unchanged |
| Second render within 50 ms | `rate_limited` | Unchanged |
| `safe_disable`, or `detach: true` | `ok` | Pin released ("detach"). The next render may reattach. |
| `safe_disable` with `lockout: true` | `ok`, then `lockout` on renders | Pin released and renders refused until `safe_disable` with `lockout: false`. |
| `direction: reverse` | `reverse_unsupported` | None. No direction pin or H-bridge is chosen. |
| Health-only build, valid render | `motor_disabled` | None |
| Bad token, duty over 100, duration over 1000 ms, bad direction | `error` `invalid_payload` | None |
| Transport `uart`, `esp32_c3_display`, wrong protocol version, over 1024 bytes | `error` | None |

Detach and lockout are different. Detach is a one-shot release. Lockout is a latch the Mac sets when the operator disables the actuator.

Software bounds, not motor ratings: duty is capped at 80 and a run at 1000 ms. A run ends by itself when `duration_ms` elapses and the pin is released.

`observed_angle_deg` and `pulse_us` in an ack echo the commanded hint. They are not an observation. `output_driven` means this firmware is driving the pin right now. It is not feedback from the motor, and no ack means the motor spun.

## Unverified

- GPIO 18 as the signal pin (the name `kServoPin` is kept because `tests/test_wayfinder.py` greps for it; the actuator is a motor). Do not move it onto GPIO 43 or 44, which are the USB-UART console. Avoid GPIO 0, 3, 19, 20, 45, 46.
- Whether the driver or ESC takes a 50 Hz pulse at all, and the duty-to-pulse mapping in the bench build.
- Whether a floating (released) signal line is safe for the driver. The sketch releases the pin; it does not drive it low.
- Motor voltage, current, stall behavior, and the driver part.
- Whether this source compiles against the real Arduino-ESP32 core.
