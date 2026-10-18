# P4X-EYE

**Source-only until a board enumerates.** This is an ESP-IDF scaffold. It has not been built or flashed, and nothing here shows a P4X-EYE has been seen by the Mac. Optional perception node in [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md).

## What the scaffold does

- Reads newline-delimited JSON from the stdio console and enforces the Mac envelope (protocol 0.1, transport `usb_debug`, at most 1024 bytes).
- Answers `health` with `ok`. Rejects everything else.
- Defines `emit_vision_event()` for a future camera task. Nothing calls it. The scaffold never invents events.

The Mac accepts `vision.event` values `user_present`, `scene_changed`, and `scene_summary`. A summary is a short untrusted string of at most 200 characters. Image, frame, jpeg, base64, and audio fields are rejected on the Mac and must never be put in a message. There is no camera code here. The board is not assumed to provide LiDAR or time-of-flight, and the Mac does not turn a camera event into a browser action.

## Flash (only when the board is in hand)

Use the USB port marked Debug. The MLH kit has one USB-C data cable.

```bash
python -m wayfinder.devices --diff        # find the new port
export WAYFINDER_P4_PORT=<the path it printed>
cd firmware/p4_eye
idf.py --list-targets                     # confirm your ESP-IDF lists esp32p4
idf.py set-target esp32p4
idf.py -p "$WAYFINDER_P4_PORT" flash
python -m wayfinder.devices --probe p4_eye
```

Unverified until tried: the ESP-IDF version that supports this P4 silicon revision, whether the Debug port carries the UART or USB Serial/JTAG console, whether stdin reads work with the default console config (`sdkconfig.defaults` is intentionally empty), and the flashing and boot-mode procedure for this board. Record what worked in `docs/hardware/sprint.md`.

Set `WAYFINDER_P4_PORT` only after `--probe` returns a typed ACK.
