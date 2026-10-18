# DevKitC-1 tactile adapter

This firmware is the physical-output side of [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md). The Mac sends `tactile.render` and `safe_disable`. The DevKit answers with `ack` or `error` on the same USB serial port.

It does not join a UART bus, and it does not accept `esp32_c3_display` as a peer.

## Flash

Board: ESP32-S3-DevKitC-1. Framework: Arduino-ESP32 3.x (`ledcAttach` / `ledcWrite`).

USB CDC on boot is disabled so the NDJSON stream uses the UART bridge:

- DevKitC-1 with one Micro-USB socket: use the USB-C-to-Micro-USB data cable.
- DevKitC-1 v1.1 with two USB-C sockets: use a USB-C data cable in the socket marked UART.

Which socket this kit has is unverified until the board is in hand.

```bash
cd firmware/devkit_tactile_adapter
pio run -t upload
pio device monitor
```

Then point the Mac at the port:

```bash
export WAYFINDER_DEVKIT_PORT=/dev/cu.usbserial-XXXX
```

The bridge chip name (`usbserial`, `wchusbserial`, `SLAB_USBtoUART`, or `usbmodem`) is unverified.

## Actuator

The actuator on hand is a 130 mini DC brushless motor (ADR-004). This sketch was written for an ESC-style 50 Hz pulse and has not been adapted, compiled, flashed, or tested for the motor. It parses `angle_deg` and ignores `duty_percent`, `duration_ms`, and `direction`. Treat the pulse below as an unverified hint, not as a held angle.

Signal is GPIO 18. That choice is unverified. If the header silkscreen makes 18 a bad land, change `kServoPin` and the Mac docs together. Do not move the signal onto GPIO 43 or 44; those are the USB-UART console. Do not use GPIO 0, 3, 19, 20, 45, or 46.

`0°` is a 1000 µs pulse and `160°` is a 2000 µs pulse, at 50 Hz. Commands closer than 50 ms are refused and the output is unchanged.

## What this sketch does not do

It does not claim the GPIO, the driver silkscreen, or the latency target have been measured. Bench latency is still an open measurement.
