# Firmware

ADR-001 retired the ESP32-C3 display target. Do not add `firmware/esp32_c3_display`.

Nothing in this tree has been flashed to a board. Every folder is **source-only until a board enumerates** on the Mac and returns a typed health ACK. Use [docs/hardware/sprint.md](../docs/hardware/sprint.md) for the order of operations.

| Board | Folder | Toolchain | State | Link to the Mac |
| --- | --- | --- | --- | --- |
| ESP32-S3-DevKitC-1 | `devkit_tactile_adapter` | PlatformIO, Arduino | Protocol source. Never compiled against the real Arduino-ESP32 core. Motor output is a separate opt-in build. | USB serial |
| ESP32-S3-BOX-3 | `esp32_s3_box3` | PlatformIO, Arduino | Scaffold. Speaks the envelope. Draws, hears, and speaks nothing. | USB serial |
| P4X-EYE | `p4_eye` | ESP-IDF | Scaffold. Speaks the envelope. No camera code. | USB debug |

All three speak the same envelope: protocol `0.1`, WIR `0.2`, newline-delimited JSON, at most 1024 bytes, source `mac_orchestrator`, ids `devkit_tactile`, `esp32_s3_box3`, `p4_eye`. They reject transport `uart`, `esp32_c3_display`, and any other protocol version. The three `main` sources each carry their own copy of that check, so a change to the envelope has to be made in all three. `tests/test_serial_contract.py` pins the strings.

Nothing is auto-detected. Find ports with `python -m wayfinder.devices`, then export `WAYFINDER_DEVKIT_PORT`, `WAYFINDER_BOX3_PORT`, or `WAYFINDER_P4_PORT` yourself. An unset variable means that board is a simulator.
