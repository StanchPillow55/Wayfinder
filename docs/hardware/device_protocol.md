# Device protocol 0.1

Transport is `usb_serial` (DevKitC-1 and BOX-3), `usb_debug` (P4X-EYE), or `simulator`. `uart` is rejected. See ADR-001.

Every message is one JSON object and a newline, at most 1024 bytes.

```json
{
  "protocol_version": "0.1",
  "transport": "usb_serial",
  "trace_id": "trace",
  "message_id": "msg",
  "sequence": 1,
  "source": "mac_orchestrator",
  "destination": "devkit_tactile",
  "type": "tactile.render",
  "created_at_ms": 0,
  "payload": {
    "state_token": "action_verified",
    "angle_deg": 125,
    "pulse_us": 1781,
    "label": "Verified state change",
    "detach": false
  },
  "requires_ack": true
}
```

Endpoints:

| ID | Board |
| --- | --- |
| `mac_orchestrator` | Mac |
| `devkit_tactile` | ESP32-S3-DevKitC-1 |
| `esp32_s3_box3` | ESP32-S3-BOX-3 |
| `p4_eye` | P4X-EYE |

`esp32_c3_display` and `esp32_s3_gateway` are retired. A message with either id, or with two board ids and no Mac, is an error.

Types the Mac sends: `tactile.render`, `safe_disable`, `display.render`, `speech.render`, `health`.

Types a board sends: `ack`, `error`, `health`, `speech.event`, `touch.event`, `vision.event`.

Pose tokens are frozen: `cleared`, `orient`, `branch_ready`, `needs_confirmation`, `action_verified`, `failure`. The actuator is a 130 mini DC brushless motor, so a pose means a motor run. `tactile.render` carries `actuator`, `duty_percent`, `duration_ms`, and `direction` (`forward`, `reverse`, `stop`). It also carries `angle_deg` and `pulse_us` as an unverified ESC-style pulse hint for firmware that has not been adapted. The hint is not an angle claim. Angles outside 0–160° and pulses outside 1000–2000 µs are rejected. A second render inside 50 ms returns `rate_limited` and does nothing. A JSON body larger than 1024 bytes is rejected. Any protocol version other than `0.1` is rejected. A broken read retries with backoff. An in-process loopback can exercise those checks; its acknowledgement is not a physical DevKit ACK.

| Pose | Duty | Duration | Direction |
| --- | --- | --- | --- |
| `cleared` | 0 | 0 ms | stop |
| `orient` | 30 | 180 ms | forward |
| `branch_ready` | 45 | 240 ms | forward |
| `needs_confirmation` | 70 | 140 ms | forward |
| `action_verified` | 55 | 360 ms | forward |
| `failure` | 80 | 120 ms | reverse |

These are renderer defaults for the simulator and the on-screen bar, not bench-verified limits.

A tactile sequence that already moved returns `duplicate_sequence` and does nothing again. `safe_disable` detaches the output and the next new sequence may reattach. Persistent lockout is a Mac status that the DevKit firmware also latches: while it is on, the Mac does not send `tactile.render`, and a firmware that receives one anyway answers `lockout`. The Mac sets the latch with `safe_disable` and `lockout: true` and releases it with `lockout: false`. A `health` ping may still be acknowledged, and a health acknowledgement is not a motor run. ADR-002 freezes this protocol at 0.1.

`vision.event` allows `event` and an optional `summary` of at most 200 characters. Fields named image, frame, audio, pcm, or base64 are rejected. The Mac records the summary as untrusted evidence and does not run a browser action from it.

`speech.event` carries a transcript only. `touch.event` carries either `selection` 1–5 or `control` of confirm, deny, or stop.

`touch.event` also allows one additive `demo_input` shape, accepted from `devkit_tactile` only: `{"demo_input": "next"}`, `{"demo_input": "back"}`, or `{"demo_input": "analog", "level": 0..100}` (integer). It comes from the DevKit demo buttons and analog input (source only, UNVERIFIED, not flashed; pins in `firmware/devkit_tactile_adapter/include/wayfinder_pins.h`). Protocol stays 0.1 and there is no new message type. The analog level only moves a highlight on the Mac. It never runs a page action.

## Board acknowledgements

An `ack` payload carries `ack_message_id` and `status`. `status` is `ok` only when the board did what was asked. Statuses a board may return:

| Status | Meaning |
| --- | --- |
| `ok` | Handled. For `health`, not a motor run. |
| `duplicate_sequence` | Same sequence as the last accepted render. Nothing done. |
| `rate_limited` | Render inside 50 ms of the last. Nothing done. |
| `lockout` | Firmware latch set by `safe_disable` with `lockout: true`. Cleared by `safe_disable` with `lockout: false`. Detach does not latch. |
| `motor_disabled` | DevKit health-only build. Protocol accepted, output pin never configured. |
| `reverse_unsupported` | `direction: reverse` has no driver scheme yet. Nothing done. |
| `not_drawn`, `not_spoken` | BOX-3 scaffold validated the message and has no panel or speaker driver. |

A board `error` carries `code` and, when known, `ack_message_id`. The Mac reports that code as the dispatch status instead of timing out. Codes: `invalid_message`, `invalid_payload`, `protocol_version`, `uart_topology_retired`, `retired_topology`, `mac_must_be_endpoint`, `transport_unsupported`, `message_too_large`.

DevKit acks also carry `duty_percent`, `duration_ms`, `direction`, `lockout`, `motor_enabled_build`, and `output_driven`. `observed_angle_deg` and `pulse_us` echo the commanded pulse hint and are not an observation. `output_driven` means the firmware is driving the signal pin now. Neither it nor any ack means the motor spun.

The Mac marks a board as having given a physical ack only for a real serial link (not a loopback) whose reply names that board as `source`. A reply from another board id is `wrong_board`.

## Port configuration

`WAYFINDER_DEVKIT_PORT`, `WAYFINDER_BOX3_PORT`, and `WAYFINDER_P4_PORT` bind a board to a serial port. Unset means a simulator. A variable that names a missing path leaves the board in `error`, not `simulator` and not `usb`. `usb` means the port opened and nothing more. `python -m wayfinder.devices` lists ports, `--diff` shows what a plug-in added, and `--probe <board_id>` sends `health` only.
