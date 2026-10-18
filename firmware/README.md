# Firmware

ADR-001 retired the ESP32-C3 display target. Do not add `firmware/esp32_c3_display`.

| Board | Role | Link to the Mac |
| --- | --- | --- |
| ESP32-S3-DevKitC-1 | One actuator (130 mini DC brushless motor, not yet adapted) | USB serial |
| ESP32-S3-BOX-3 | Display, touch, microphones, speaker | USB serial |
| P4X-EYE | Camera events | USB debug |

The DevKit sketch is in `devkit_tactile_adapter`. BOX-3 and P4X-EYE adapters currently live on the Mac; their folders document the contract those boards will speak.
