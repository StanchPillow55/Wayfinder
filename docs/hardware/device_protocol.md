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

A tactile sequence that already moved returns `duplicate_sequence` and does nothing again. `safe_disable` detaches the output and the next new sequence may reattach. Persistent lockout is a Mac status: while it is on, the Mac does not send `tactile.render`. A `health` ping may still be acknowledged, and a health acknowledgement is not a motor run. ADR-002 freezes this protocol at 0.1.

`vision.event` allows `event` and an optional `summary` of at most 200 characters. Fields named image, frame, audio, pcm, or base64 are rejected. The Mac records the summary as untrusted evidence and does not run a browser action from it.

`speech.event` carries a transcript only. `touch.event` carries either `selection` 1–5 or `control` of confirm, deny, or stop.
