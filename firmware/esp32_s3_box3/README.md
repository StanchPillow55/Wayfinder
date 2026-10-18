# ESP32-S3-BOX-3

Optional human I/O surface in [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md).

The Mac already accepts and emits the BOX-3 messages:

- inbound `speech.event` with a transcript
- inbound `touch.event` with a branch number or confirm / deny / stop
- outbound `display.render` and `speech.render`

Those messages travel on the BOX-3 USB-C port (power, program, and serial on the main unit). The dock's second USB-C is power only. There is no cable from this board to the DevKit or to P4X-EYE.

This folder does not contain flashed display or microphone firmware. Driving the 2.4 inch panel and the ES7210 microphones needs the ESP-BOX board support package, and that port is still unverified. Until it exists, the Mac UI is the display, touch, and speech fallback. The simulator speaks the same JSON the future firmware will speak.

Set `WAYFINDER_BOX3_PORT` when a serial firmware is actually on the board.
