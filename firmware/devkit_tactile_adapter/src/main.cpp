// Wayfinder physical-output adapter for the ESP32-S3-DevKitC-1.
//
// STATUS: SOURCE ONLY. This file has not been compiled, flashed, or run on a
// board. No physical ACK from this firmware exists until a DevKit enumerates
// on the Mac and answers a typed health ping (see docs/hardware/sprint.md).
//
// ADR-001: the Mac is the orchestrator. This board is one actuator output.
// It does not browse, run a model, or talk to BOX-3 or P4X-EYE.
// Link: USB serial, newline-delimited JSON, protocol 0.1, max 1024 bytes.
// Rejects transport "uart" and endpoint "esp32_c3_display".
//
// ACTUATOR: a 130 mini DC brushless motor (ADR-004). It spins; it does not
// hold an angle. angle_deg / pulse_us are only an unverified ESC-style pulse
// hint. The semantic command is duty_percent, duration_ms, direction.
//
// TWO BUILDS (see platformio.ini):
//   devkit_health_only   WAYFINDER_MOTOR_ENABLE=0 (default). Never configures
//                        the signal pin. tactile.render runs every protocol
//                        check, then answers "motor_disabled". Safe to flash
//                        with nothing wired.
//   devkit_motor_bench   WAYFINDER_MOTOR_ENABLE=1. Drives the signal pin. Only
//                        flash it after the meter checks in the sprint runbook,
//                        and only after a typed health ACK from the first build.
//
// DEMO INPUTS (both builds, UNVERIFIED pins, DEMO MAP NOT A CLINICAL DEVICE):
//   three active-low buttons (INPUT_PULLUP to GND) and one analog level.
//   They are configured as inputs only. The motor signal pin is never touched
//   by this code, in either build. Nothing here proves the motor rotated or
//   that a blind person can complete a task.
//   Protocol stays 0.1 and no message type is added. Board -> Mac, using the
//   existing touch.event, newline-delimited JSON, source devkit_tactile:
//     button A (press)            {"demo_input":"next"}            next branch
//     button B (press)            {"control":"confirm"}            confirm (existing shape)
//     button C (short press)      {"demo_input":"back"}            back
//     button C (held >= 800 ms)   {"control":"stop"}               stop (existing shape)
//     analog (changed, throttled) {"demo_input":"analog","level":0..100}
//                                 low to high moves along the branch list
//   "demo_input" is the one additive touch.event payload shape. The Mac accepts
//   it from this board only (models._validate_payload, orchestrator._ingest) and
//   maps it onto the same actions as the keyboard path. The analog level only
//   moves a highlight on the Mac. It never runs a page action by itself.
//   A floating analog pin reads noise; a deadband and a stable-read check keep
//   most of it off the wire, and the worst case is a moved highlight.
//
// Everything marked UNVERIFIED below is an assumption, not a bench fact.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

#include "wayfinder_pins.h"  // UNVERIFIED bench pin map; names numbers only

#ifndef WAYFINDER_MOTOR_ENABLE
#define WAYFINDER_MOTOR_ENABLE 0
#endif

// UNVERIFIED: GPIO 18 is a software assumption. The name kServoPin is kept
// because tests/test_wayfinder.py greps for it; the actuator is a motor.
static constexpr int kServoPin = 18;
static_assert(kServoPin == kBenchSignalGpioUnverified,
              "update include/wayfinder_pins.h and docs/hardware/breadboard.md together");
[[maybe_unused]] static constexpr int kMotorSignalPin = kServoPin;
// UNVERIFIED: 50 Hz ESC-style pulse hint. Whether the driver wants this at all
// is an open bench question (docs/hardware/pinouts.md).
[[maybe_unused]] static constexpr int kPwmHz = 50;
[[maybe_unused]] static constexpr int kResBits = 14;
// LEDC channel for the signal pin. The installed Arduino-ESP32 core (2.x,
// platform espressif32 7.x) uses the channel-based LEDC API (setup/attach-pin/
// write-channel/detach-pin) rather than the 3.x pin-based one. The actual calls
// live only inside the motor output stage below. Channel 0 is unused elsewhere.
[[maybe_unused]] static constexpr int kPwmChannel = 0;
static constexpr int kMinPulseUs = 1000;
static constexpr int kMaxPulseUs = 2000;
static constexpr int kMaxAngle = 160;
static constexpr uint32_t kMinIntervalMs = 50;
static constexpr size_t kMaxBytes = 1024;

// Software bounds only. These are not motor ratings.
static constexpr int kMaxDutyPercent = 80;  // matches the largest MOTOR_RUNS duty
static constexpr uint32_t kMaxRunMs = 1000;

static constexpr const char* kFirmwareName = "devkit_tactile 0.1 source-only";

struct MotorRun {
  int duty;
  uint32_t durationMs;
  const char* direction;  // "forward", "reverse", "stop"
  int hintAngle;          // unverified pulse hint
};

// Mirrors MOTOR_RUNS and POSES in src/wayfinder/contracts.py. ADR-002 froze the tokens.
static bool poseRun(const char* token, MotorRun* out) {
  if (!token) return false;
  if (strcmp(token, "cleared") == 0) { *out = {0, 0, "stop", 0}; return true; }
  if (strcmp(token, "orient") == 0) { *out = {30, 180, "forward", 30}; return true; }
  if (strcmp(token, "branch_ready") == 0) { *out = {45, 240, "forward", 55}; return true; }
  if (strcmp(token, "needs_confirmation") == 0) { *out = {70, 140, "forward", 90}; return true; }
  if (strcmp(token, "action_verified") == 0) { *out = {55, 360, "forward", 125}; return true; }
  if (strcmp(token, "failure") == 0) { *out = {80, 120, "reverse", 155}; return true; }
  return false;
}

[[maybe_unused]] static bool gAttached = false;
static bool gOutputActive = false;   // pin currently driven by this firmware
static bool gLockout = false;        // latched by safe_disable with lockout:true
static uint32_t gRunEndMs = 0;
static uint32_t gLastMoveMs = 0;
static uint32_t gOutSeq = 0;
static bool gHaveApplied = false;
static uint32_t gLastAppliedSequence = 0;
static int gHintAngle = 0;
static int gDuty = 0;
static uint32_t gDuration = 0;
static const char* gDirection = "stop";

static int angleToPulse(int angle) {
  if (angle < 0) angle = 0;
  if (angle > kMaxAngle) angle = kMaxAngle;
  return kMinPulseUs + (angle * (kMaxPulseUs - kMinPulseUs) / kMaxAngle);
}

// ---- output stage ---------------------------------------------------------
// In the health-only build every function here is a no-op. The pin is never
// configured, so nothing can be driven by this firmware.

[[maybe_unused]] static void writePulse(int pulseUs) {
#if WAYFINDER_MOTOR_ENABLE
  if (pulseUs < kMinPulseUs) pulseUs = kMinPulseUs;
  if (pulseUs > kMaxPulseUs) pulseUs = kMaxPulseUs;
  if (!gAttached) {
    ledcSetup(kPwmChannel, kPwmHz, kResBits);
    ledcAttachPin(kMotorSignalPin, kPwmChannel);
    gAttached = true;
  }
  const uint32_t maxDuty = (1u << kResBits) - 1u;
  const uint32_t duty = static_cast<uint32_t>(pulseUs) * maxDuty / 20000u;
  ledcWrite(kPwmChannel, duty);
  gOutputActive = true;
#else
  (void)pulseUs;
#endif
}

// Release the pin. This is "detach": it is not a lockout and it is not a
// claim that the motor has stopped. The Mac may send the next render at once.
static void detachOutput() {
#if WAYFINDER_MOTOR_ENABLE
  if (gAttached) {
    ledcDetachPin(kMotorSignalPin);
    gAttached = false;
  }
  pinMode(kMotorSignalPin, INPUT);
#endif
  gOutputActive = false;
  gHintAngle = 0;
  gDuty = 0;
  gDuration = 0;
  gDirection = "stop";
  gRunEndMs = 0;
}

// ---- wire output ----------------------------------------------------------

static void sendMessage(const char* type, const char* traceId, uint32_t sequence, JsonObject payload) {
  char messageId[24];
  snprintf(messageId, sizeof(messageId), "dk%lu", static_cast<unsigned long>(gOutSeq++));
  JsonDocument doc;
  doc["protocol_version"] = "0.1";
  doc["transport"] = "usb_serial";
  doc["trace_id"] = traceId;
  doc["message_id"] = messageId;
  doc["sequence"] = sequence;
  doc["source"] = "devkit_tactile";
  doc["destination"] = "mac_orchestrator";
  doc["type"] = type;
  doc["created_at_ms"] = millis();
  doc["payload"] = payload;
  doc["requires_ack"] = false;
  serializeJson(doc, Serial);
  Serial.print('\n');
}

static void fillState(JsonObject payload) {
  // observed_angle_deg and pulse_us stay for Mac compatibility. They echo the
  // commanded pulse hint. They are NOT an observation of the motor.
  payload["observed_angle_deg"] = gHintAngle;
  payload["pulse_us"] = angleToPulse(gHintAngle);
  payload["duty_percent"] = gDuty;
  payload["duration_ms"] = gDuration;
  payload["direction"] = gDirection;
  payload["lockout"] = gLockout;
  payload["motor_enabled_build"] = (WAYFINDER_MOTOR_ENABLE != 0);
  // True only while this firmware is driving the signal pin. Not feedback from the motor.
  payload["output_driven"] = gOutputActive;
}

static void sendAck(const char* traceId, uint32_t sequence, const char* ackId, const char* status, const char* token) {
  JsonDocument payload;
  payload["ack_message_id"] = ackId;
  payload["status"] = status;
  payload["state_token"] = token ? token : "cleared";
  fillState(payload.as<JsonObject>());
  sendMessage("ack", traceId, sequence, payload.as<JsonObject>());
}

static void sendError(const char* traceId, uint32_t sequence, const char* ackId, const char* code) {
  JsonDocument payload;
  payload["code"] = code;
  if (ackId && ackId[0]) payload["ack_message_id"] = ackId;
  sendMessage("error", traceId, sequence, payload.as<JsonObject>());
}

// ---- demo inputs: three buttons and one analog level ----------------------
// UNVERIFIED pins, named once in include/wayfinder_pins.h (the bench pin map).
// This file does not pick numbers of its own. The map avoids GPIO 18 (motor
// signal assumption), strapping and USB pins, and the console pair.
static constexpr int kButtonAPin = kBenchButton1GpioUnverified;  // next branch
static constexpr int kButtonBPin = kBenchButton2GpioUnverified;  // confirm
static constexpr int kButtonCPin = kBenchButton3GpioUnverified;  // back, held = stop
static constexpr int kAnalogPin = kBenchAnalogGpioUnverified;    // branch position, low to high
static_assert(kButtonAPin != kServoPin && kButtonBPin != kServoPin &&
              kButtonCPin != kServoPin && kAnalogPin != kServoPin,
              "demo inputs must not share the motor signal pin");

static constexpr uint32_t kDebounceMs = 30;
static constexpr uint32_t kLongPressMs = 800;
static constexpr uint32_t kAnalogPollMs = 40;
static constexpr uint32_t kAnalogMinSendMs = 200;
static constexpr int kAnalogDeadband = 8;   // percent of full scale
static constexpr int kAnalogStableBand = 3; // percent; reads must agree this closely
static constexpr int kAnalogStableReads = 3;
static constexpr int kAdcMax = 4095;        // 12-bit default

struct DemoButton {
  int pin;
  bool raw;          // last raw sample, true = pressed
  bool stable;       // debounced state
  uint32_t changedMs;
  uint32_t pressedMs;
  bool longSent;
};

static DemoButton gButtons[3] = {
    {kButtonAPin, false, false, 0, 0, false},
    {kButtonBPin, false, false, 0, 0, false},
    {kButtonCPin, false, false, 0, 0, false},
};
static uint32_t gInputSeq = 0;
static int gAnalogSent = -1;      // last level reported, -1 until the first stable read
static int gAnalogCandidate = -1;
static int gAnalogStableCount = 0;
static uint32_t gAnalogPollMsAt = 0;
static uint32_t gAnalogSentMsAt = 0;

static void sendTouchInput(const char* key, const char* value, int level) {
  JsonDocument payload;
  payload[key] = value;
  if (level >= 0) payload["level"] = level;
  sendMessage("touch.event", "tactile-input", gInputSeq++, payload.as<JsonObject>());
}

static void setupInputs() {
  // Inputs only. The motor signal pin is not configured here.
  for (auto& b : gButtons) {
    pinMode(b.pin, INPUT_PULLUP);
    b.raw = b.stable = (digitalRead(b.pin) == LOW);
    b.changedMs = b.pressedMs = millis();
  }
  analogReadResolution(12);
  // Seed from the first reading so boot does not move a highlight.
  gAnalogSent = static_cast<int>(static_cast<long>(analogRead(kAnalogPin)) * 100 / kAdcMax);
}

static void onButtonPressed(int index) {
  if (index == 0) sendTouchInput("demo_input", "next", -1);
  else if (index == 1) sendTouchInput("control", "confirm", -1);
  // Button C waits for release (back) or the long-press timer (stop).
}

static void pollButtons(uint32_t now) {
  for (int i = 0; i < 3; i++) {
    DemoButton& b = gButtons[i];
    const bool sample = (digitalRead(b.pin) == LOW);
    if (sample != b.raw) {
      b.raw = sample;
      b.changedMs = now;
    }
    if (b.raw != b.stable && now - b.changedMs >= kDebounceMs) {
      b.stable = b.raw;
      if (b.stable) {
        b.pressedMs = now;
        b.longSent = false;
        onButtonPressed(i);
      } else if (i == 2 && !b.longSent) {
        sendTouchInput("demo_input", "back", -1);
      }
    }
    if (i == 2 && b.stable && !b.longSent && now - b.pressedMs >= kLongPressMs) {
      b.longSent = true;
      sendTouchInput("control", "stop", -1);
    }
  }
}

static void pollAnalog(uint32_t now) {
  if (now - gAnalogPollMsAt < kAnalogPollMs) return;
  gAnalogPollMsAt = now;
  long sum = 0;
  for (int i = 0; i < 4; i++) sum += analogRead(kAnalogPin);
  int level = static_cast<int>(sum / 4 * 100 / kAdcMax);
  if (level < 0) level = 0;
  if (level > 100) level = 100;
  // Require a few agreeing reads before trusting a new value.
  if (gAnalogCandidate < 0 || abs(level - gAnalogCandidate) > kAnalogStableBand) {
    gAnalogCandidate = level;
    gAnalogStableCount = 1;
    return;
  }
  if (gAnalogStableCount < kAnalogStableReads) {
    gAnalogStableCount++;
    if (gAnalogStableCount < kAnalogStableReads) return;
  }
  const int diff = abs(gAnalogCandidate - gAnalogSent);
  const bool atEnd = (gAnalogCandidate <= 2 && gAnalogSent > 2) || (gAnalogCandidate >= 98 && gAnalogSent < 98);
  if ((diff >= kAnalogDeadband || atEnd) && now - gAnalogSentMsAt >= kAnalogMinSendMs) {
    gAnalogSent = gAnalogCandidate;
    gAnalogSentMsAt = now;
    sendTouchInput("demo_input", "analog", gAnalogSent);
  }
}

static void pollInputs() {
  const uint32_t now = millis();
  pollButtons(now);
  pollAnalog(now);
}

// ---- command handling -----------------------------------------------------

static bool readIntField(JsonObject payload, const char* key, long* out) {
  JsonVariant v = payload[key];
  if (v.isNull()) return false;
  if (!v.is<long>()) return false;
  *out = v.as<long>();
  return true;
}

static void handleTactile(const char* traceId, uint32_t sequence, const char* messageId, JsonObject payload) {
  const char* token = payload["state_token"] | "cleared";
  const bool detach = payload["detach"] | false;

  MotorRun run;
  if (!poseRun(token, &run)) {
    sendError(traceId, sequence, messageId, "invalid_payload");
    return;
  }

  // Semantic fields win when present. Otherwise the frozen pose table applies.
  long value = 0;
  if (!payload["duty_percent"].isNull()) {
    if (!readIntField(payload, "duty_percent", &value) || value < 0 || value > 100) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    run.duty = static_cast<int>(value);
  }
  if (!payload["duration_ms"].isNull()) {
    if (!readIntField(payload, "duration_ms", &value) || value < 0 || value > static_cast<long>(kMaxRunMs)) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    run.durationMs = static_cast<uint32_t>(value);
  }
  if (!payload["direction"].isNull()) {
    const char* dir = payload["direction"] | "";
    if (strcmp(dir, "forward") != 0 && strcmp(dir, "reverse") != 0 && strcmp(dir, "stop") != 0) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    run.direction = (strcmp(dir, "forward") == 0) ? "forward" : (strcmp(dir, "reverse") == 0 ? "reverse" : "stop");
  }
  // Legacy hint, bounds-checked like the Mac does. It does not choose the run.
  if (!payload["angle_deg"].isNull()) {
    if (!readIntField(payload, "angle_deg", &value) || value < 0 || value > kMaxAngle) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    run.hintAngle = static_cast<int>(value);
  }
  if (!payload["pulse_us"].isNull()) {
    if (!readIntField(payload, "pulse_us", &value) || value < kMinPulseUs || value > kMaxPulseUs) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
  }
  if (run.duty > kMaxDutyPercent) run.duty = kMaxDutyPercent;

  // detach is not lockout. Detach releases the pin; the next render may reattach.
  if (detach) {
    detachOutput();
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }
  // lockout is latched until a safe_disable with lockout:false. Nothing moves.
  if (gLockout) {
    sendAck(traceId, sequence, messageId, "lockout", token);
    return;
  }

  const uint32_t now = millis();
  if (gHaveApplied && sequence == gLastAppliedSequence) {
    sendAck(traceId, sequence, messageId, "duplicate_sequence", token);
    return;
  }
  if (gHaveApplied && now - gLastMoveMs < kMinIntervalMs) {
    sendAck(traceId, sequence, messageId, "rate_limited", token);
    return;
  }

  const bool stop = run.duty == 0 || run.durationMs == 0 || strcmp(run.direction, "stop") == 0;
  // No direction pin or H-bridge is chosen. Reverse is refused, not guessed.
  if (!stop && strcmp(run.direction, "reverse") == 0) {
    sendAck(traceId, sequence, messageId, "reverse_unsupported", token);
    return;
  }

  gLastMoveMs = now;
  gHaveApplied = true;
  gLastAppliedSequence = sequence;

  if (stop) {
    detachOutput();
    sendAck(traceId, sequence, messageId, "ok", token);
    return;
  }
#if WAYFINDER_MOTOR_ENABLE
  gHintAngle = run.hintAngle;
  gDuty = run.duty;
  gDuration = run.durationMs;
  gDirection = run.direction;
  // UNVERIFIED mapping: duty 0..100 onto the 1000..2000 us pulse hint.
  writePulse(kMinPulseUs + run.duty * (kMaxPulseUs - kMinPulseUs) / 100);
  gRunEndMs = now + run.durationMs;
  if (gRunEndMs == 0) gRunEndMs = 1;
  sendAck(traceId, sequence, messageId, "ok", token);
#else
  // Health-only build: protocol accepted, pin untouched.
  gHintAngle = 0;
  gDuty = 0;
  gDuration = 0;
  gDirection = "stop";
  sendAck(traceId, sequence, messageId, "motor_disabled", token);
#endif
}

static void handleLine(const char* line, size_t len) {
  JsonDocument doc;
  if (deserializeJson(doc, line, len)) {
    sendError("none", 0, "", "invalid_message");
    return;
  }
  const char* transport = doc["transport"] | "";
  const char* source = doc["source"] | "";
  const char* destination = doc["destination"] | "";
  const char* type = doc["type"] | "";
  const char* traceId = doc["trace_id"] | "none";
  const char* messageId = doc["message_id"] | "";
  const uint32_t sequence = doc["sequence"] | 0;

  if (strcmp(transport, "uart") == 0) {
    sendError(traceId, sequence, messageId, "uart_topology_retired");
    return;
  }
  const char* version = doc["protocol_version"] | "";
  if (strcmp(version, "0.1") != 0) {
    sendError(traceId, sequence, messageId, "protocol_version");
    return;
  }
  if (strcmp(source, "esp32_c3_display") == 0 || strcmp(destination, "esp32_c3_display") == 0) {
    sendError(traceId, sequence, messageId, "retired_topology");
    return;
  }
  if (strcmp(destination, "devkit_tactile") != 0 || strcmp(source, "mac_orchestrator") != 0) {
    sendError(traceId, sequence, messageId, "mac_must_be_endpoint");
    return;
  }
  if (strcmp(transport, "usb_serial") != 0) {
    sendError(traceId, sequence, messageId, "transport_unsupported");
    return;
  }

  if (strcmp(type, "health") == 0) {
    // A health ack is not a motor run. It never touches the output stage.
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }
  if (strcmp(type, "safe_disable") == 0) {
    detachOutput();
    JsonObject p = doc["payload"].as<JsonObject>();
    if (!p.isNull() && !p["lockout"].isNull()) {
      gLockout = p["lockout"] | false;
    }
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }
  if (strcmp(type, "tactile.render") != 0) {
    sendError(traceId, sequence, messageId, "invalid_payload");
    return;
  }
  JsonObject payload = doc["payload"].as<JsonObject>();
  if (payload.isNull()) {
    sendError(traceId, sequence, messageId, "invalid_payload");
    return;
  }
  handleTactile(traceId, sequence, messageId, payload);
}

// ---- serial framing -------------------------------------------------------

static char gLine[kMaxBytes + 1];
static size_t gLen = 0;
static bool gOverflow = false;

static void pumpSerial() {
  while (Serial.available() > 0) {
    const int c = Serial.read();
    if (c < 0) return;
    if (c == '\r') continue;
    if (c == '\n') {
      if (gOverflow) {
        sendError("none", 0, "", "message_too_large");
      } else if (gLen > 0) {
        gLine[gLen] = '\0';
        handleLine(gLine, gLen);
      }
      gLen = 0;
      gOverflow = false;
      continue;
    }
    if (gLen >= kMaxBytes) {
      gOverflow = true;  // discard until newline
      continue;
    }
    gLine[gLen++] = static_cast<char>(c);
  }
}

void setup() {
  Serial.begin(115200);
  // The signal pin is deliberately not configured here in either build.
  // The demo inputs below are inputs only.
  setupInputs();
  delay(200);
  JsonDocument payload;
  payload["board"] = "ESP32-S3-DevKitC-1";
  payload["firmware"] = kFirmwareName;
  payload["status"] = "ready";
  payload["motor_enabled_build"] = (WAYFINDER_MOTOR_ENABLE != 0);
#if WAYFINDER_MOTOR_ENABLE
  payload["gpio_assumed_unverified"] = kMotorSignalPin;
#endif
  sendMessage("health", "boot", 0, payload.as<JsonObject>());
}

void loop() {
  pumpSerial();
  pollInputs();
  // Non-blocking run timer: release the output when duration_ms has elapsed.
  if (gOutputActive && gRunEndMs != 0 && static_cast<int32_t>(millis() - gRunEndMs) >= 0) {
    detachOutput();
  }
}
