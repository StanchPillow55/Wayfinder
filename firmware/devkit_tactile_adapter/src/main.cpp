// Wayfinder physical-output adapter for the ESP32-S3-DevKitC-1.
// ADR-001: the Mac is the orchestrator. This board drives one servo.
// It does not browse, run a model, or talk to BOX-3 or P4X-EYE.
// Link: USB serial, newline-delimited JSON, protocol 0.1.
// Rejects transport "uart" and endpoint "esp32_c3_display".

#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdio.h>

static constexpr int kServoPin = 18;
static constexpr int kPwmHz = 50;
static constexpr int kResBits = 14;
static constexpr int kMinPulseUs = 1000;
static constexpr int kMaxPulseUs = 2000;
static constexpr int kMaxAngle = 160;
static constexpr uint32_t kMinIntervalMs = 50;
static constexpr size_t kMaxBytes = 1024;

static bool gAttached = false;
static int gAngle = 0;
static uint32_t gLastMoveMs = 0;
static uint32_t gSequence = 0;
static bool gHaveApplied = false;
static uint32_t gLastAppliedSequence = 0;

static int angleToPulse(int angle) {
  if (angle < 0) angle = 0;
  if (angle > kMaxAngle) angle = kMaxAngle;
  return kMinPulseUs + (angle * (kMaxPulseUs - kMinPulseUs) / kMaxAngle);
}

static int poseAngle(const char* token) {
  if (!token) return 0;
  if (strcmp(token, "cleared") == 0) return 0;
  if (strcmp(token, "orient") == 0) return 30;
  if (strcmp(token, "branch_ready") == 0) return 55;
  if (strcmp(token, "needs_confirmation") == 0) return 90;
  if (strcmp(token, "action_verified") == 0) return 125;
  if (strcmp(token, "failure") == 0) return 155;
  return -1;
}

static void attachPwm() {
  if (gAttached) return;
  ledcAttach(kServoPin, kPwmHz, kResBits);
  gAttached = true;
}

static void writePulse(int pulseUs) {
  if (pulseUs < kMinPulseUs) pulseUs = kMinPulseUs;
  if (pulseUs > kMaxPulseUs) pulseUs = kMaxPulseUs;
  attachPwm();
  const uint32_t maxDuty = (1u << kResBits) - 1u;
  const uint32_t duty = static_cast<uint32_t>(pulseUs) * maxDuty / 20000u;
  ledcWrite(kServoPin, duty);
}

static void detachPwm() {
  if (gAttached) {
    ledcDetach(kServoPin);
    gAttached = false;
  }
  gAngle = 0;
  pinMode(kServoPin, INPUT);
}

static void sendMessage(const char* type, const char* traceId, uint32_t sequence, JsonObject payload) {
  char messageId[24];
  snprintf(messageId, sizeof(messageId), "dk%lu", static_cast<unsigned long>(gSequence++));
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

static void sendAck(const char* traceId, uint32_t sequence, const char* ackId, const char* status, const char* token) {
  JsonDocument payload;
  payload["ack_message_id"] = ackId;
  payload["status"] = status;
  payload["observed_angle_deg"] = gAngle;
  payload["state_token"] = token ? token : "cleared";
  payload["pulse_us"] = angleToPulse(gAngle);
  sendMessage("ack", traceId, sequence, payload.as<JsonObject>());
}

static void sendError(const char* traceId, uint32_t sequence, const char* code) {
  JsonDocument payload;
  payload["code"] = code;
  sendMessage("error", traceId, sequence, payload.as<JsonObject>());
}

static void applyAngle(int angle) {
  if (angle < 0) angle = 0;
  if (angle > kMaxAngle) angle = kMaxAngle;
  gAngle = angle;
  writePulse(angleToPulse(angle));
}

static void handleLine(const String& line) {
  if (line.length() > kMaxBytes) {
    sendError("none", 0, "message_too_large");
    return;
  }
  JsonDocument doc;
  if (deserializeJson(doc, line)) {
    sendError("none", 0, "invalid_message");
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
    sendError(traceId, sequence, "uart_topology_retired");
    return;
  }
  const char* version = doc["protocol_version"] | "";
  if (strcmp(version, "0.1") != 0) {
    sendError(traceId, sequence, "protocol_version");
    return;
  }
  if (strcmp(source, "esp32_c3_display") == 0 || strcmp(destination, "esp32_c3_display") == 0) {
    sendError(traceId, sequence, "retired_topology");
    return;
  }
  if (strcmp(destination, "devkit_tactile") != 0 || strcmp(source, "mac_orchestrator") != 0) {
    sendError(traceId, sequence, "mac_must_be_endpoint");
    return;
  }

  if (strcmp(type, "health") == 0) {
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }
  if (strcmp(type, "safe_disable") == 0) {
    detachPwm();
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }
  if (strcmp(type, "tactile.render") != 0) {
    sendError(traceId, sequence, "invalid_payload");
    return;
  }

  JsonObject payload = doc["payload"].as<JsonObject>();
  const bool detach = payload["detach"] | false;
  const char* token = payload["state_token"] | "cleared";
  if (detach) {
    detachPwm();
    sendAck(traceId, sequence, messageId, "ok", "cleared");
    return;
  }

  int angle = payload["angle_deg"] | poseAngle(token);
  if (angle < 0) {
    sendError(traceId, sequence, "invalid_payload");
    return;
  }
  const uint32_t now = millis();
  if (gHaveApplied && sequence == gLastAppliedSequence) {
    sendAck(traceId, sequence, messageId, "duplicate_sequence", token);
    return;
  }
  if (now - gLastMoveMs < kMinIntervalMs) {
    sendAck(traceId, sequence, messageId, "rate_limited", token);
    return;
  }
  gLastMoveMs = now;
  gHaveApplied = true;
  gLastAppliedSequence = sequence;
  applyAngle(angle);
  sendAck(traceId, sequence, messageId, "ok", token);
}

void setup() {
  Serial.begin(115200);
  pinMode(kServoPin, INPUT);
  delay(200);
  JsonDocument payload;
  payload["board"] = "ESP32-S3-DevKitC-1";
  payload["gpio"] = kServoPin;
  payload["status"] = "ready";
  sendMessage("health", "boot", 0, payload.as<JsonObject>());
}

void loop() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  handleLine(line);
}
