# ESP32-S3-BOX-3

**Source-only until a board enumerates.** `src/main.cpp` is a scaffold. It has not been flashed, and nothing here shows a BOX-3 has been seen by the Mac. Optional human I/O surface in [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md).

## What the scaffold does

- Speaks the Mac envelope (protocol 0.1, `usb_serial`, newline-delimited JSON, at most 1024 bytes).
- Answers `health` with `ok`.
- Validates `display.render` and `speech.render`, then answers `not_drawn` or `not_spoken`. The Mac treats anything other than `ok` as unacknowledged, which is correct: no panel or speaker driver exists.
- Defines emitters for `touch.event` (selection 1 to 5, or control confirm, deny, stop) and `speech.event` (transcript text only). Nothing calls them. The scaffold never invents input.
- Rejects `uart`, `esp32_c3_display`, and other protocol versions.

It does not drive the 2.4 inch panel, read touch, capture audio, or play audio. That needs the ESP-BOX board support package, and that port is unverified. Until it exists, the Mac UI is the display, touch, and speech fallback, and the simulator speaks the same JSON.

There is no cable from this board to the DevKit or to P4X-EYE. BOX-3 USB-C on the main unit is power and data. The dock's second USB-C is power only.

## Flash (only when the board is in hand)

```bash
python -m wayfinder.devices --diff        # plug in the BOX-3 data cable, find the new port
export WAYFINDER_BOX3_PORT=<the path it printed>
cd firmware/esp32_s3_box3
pio run -t upload --upload-port "$WAYFINDER_BOX3_PORT"
python -m wayfinder.devices --probe esp32_s3_box3
```

Unverified until tried: the PlatformIO `board` entry (`esp32s3box` is a guess; check `pio boards box`), the USB mode flags in `platformio.ini`, and whether the ROM bootloader needs the BOX-3 download mode (hold the Boot button while powering) to accept an upload.

Set `WAYFINDER_BOX3_PORT` only after `--probe` returns a typed ACK. A boot `health` line with `"scaffold": true` is expected and means exactly that: a scaffold is running.
