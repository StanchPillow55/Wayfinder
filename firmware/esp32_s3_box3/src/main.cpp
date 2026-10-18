// Wayfinder ESP32-S3-BOX-3 adapter SCAFFOLD.
//
// STATUS: SOURCE ONLY. Not compiled, not flashed, not run on a board. Do not
// treat this file as evidence that a BOX-3 has been seen by the Mac. Nothing
// is "flashed" until the board enumerates as a /dev/cu.* port and
// `python -m wayfinder.devices --probe esp32_s3_box3` gets a typed ACK.
//
// What this scaffold does:
//   - speaks the Mac envelope (protocol 0.1, newline-delimited JSON, <= 1024 B)
//   - answers health with ok
//   - accepts display.render and speech.render, validates them, and replies
//     "not_drawn" / "not_spoken" because no panel, speaker, touch, or
//     microphone driver is written. The Mac treats any status other than "ok"
//     as unacknowledged, which is the truth.
//   - defines touch.event / speech.event emitters that nothing calls yet.
//     The scaffold never invents input.
//
// What it does not do: drive the 2.4 inch panel, read touch, capture audio,
// play audio, or talk to the DevKit or P4X-EYE. ADR-001: the Mac is the only
// peer. Rejects transport "uart" and endpoint "esp32_c3_display".
//
// UNVERIFIED: which USB-C port and which Arduino core settings expose serial
// on the BOX-3, and the BSP needed for panel and mics.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

#include "box3_pins.h"  // UNVERIFIED button GPIOs; input only, active low

static constexpr size_t kMaxBytes = 1024;
static constexpr const char* kSelfId = "esp32_s3_box3";
static constexpr const char* kFirmwareName = "box3_scaffold 0.1 source-only";

static uint32_t gOutSeq = 0;

static void sendMessage(const char* type, const char* traceId, uint32_t sequence, JsonObject payload) {
  char messageId[24];
  snprintf(messageId, sizeof(messageId), "bx%lu", static_cast<unsigned long>(gOutSeq++));
  JsonDocument doc;
  doc["protocol_version"] = "0.1";
  doc["transport"] = "usb_serial";
  doc["trace_id"] = traceId;
  doc["message_id"] = messageId;
  doc["sequence"] = sequence;
  doc["source"] = kSelfId;
  doc["destination"] = "mac_orchestrator";
  doc["type"] = type;
  doc["created_at_ms"] = millis();
  doc["payload"] = payload;
  doc["requires_ack"] = false;
  serializeJson(doc, Serial);
  Serial.print('\n');
}

static void sendAck(const char* traceId, uint32_t sequence, const char* ackId, const char* status) {
  JsonDocument payload;
  payload["ack_message_id"] = ackId;
  payload["status"] = status;
  payload["state_token"] = "cleared";
  payload["display_driver"] = false;
  payload["touch_driver"] = false;
  payload["speaker_driver"] = false;
  sendMessage("ack", traceId, sequence, payload.as<JsonObject>());
}

static void sendError(const char* traceId, uint32_t sequence, const char* ackId, const char* code) {
  JsonDocument payload;
  payload["code"] = code;
  if (ackId && ackId[0]) payload["ack_message_id"] = ackId;
  sendMessage("error", traceId, sequence, payload.as<JsonObject>());
}

// Emitters for the board to Mac direction. Nothing calls these. A real
// firmware calls them from a touch or speech-to-text hook. touch.event carries
// either selection 1..5 or control confirm|deny|stop, never both.
[[maybe_unused]] static void emitTouchSelection(int selection) {
  if (selection < 1 || selection > 5) return;
  JsonDocument payload;
  payload["selection"] = selection;
  sendMessage("touch.event", "touch", 0, payload.as<JsonObject>());
}

[[maybe_unused]] static void emitTouchControl(const char* control) {
  if (!control) return;
  if (strcmp(control, "confirm") != 0 && strcmp(control, "deny") != 0 && strcmp(control, "stop") != 0) return;
  JsonDocument payload;
  payload["control"] = control;
  sendMessage("touch.event", "touch", 0, payload.as<JsonObject>());
}

// Transcript text only, at most 500 characters. Never raw audio, PCM, or base64.
[[maybe_unused]] static void emitSpeechTranscript(const char* transcript) {
  if (!transcript || !transcript[0] || strlen(transcript) > 500) return;
  JsonDocument payload;
  payload["transcript"] = transcript;
  sendMessage("speech.event", "speech", 0, payload.as<JsonObject>());
}

// ---- physical buttons -> touch.event control ------------------------------
// Three momentary buttons (active low, INPUT_PULLUP) call the existing
// emitTouchControl. Input only; this never drives a pin. GPIO0 (confirm) is the
// confident BOOT button; the other two are UNVERIFIED (box3_pins.h).
static constexpr uint32_t kBtnDebounceMs = 30;

struct Box3Button {
  int pin;
  const char* control;  // "confirm" | "deny" | "stop"
  bool stable;          // debounced state, true = pressed
  bool raw;
  uint32_t changedMs;
};

static Box3Button gBox3Buttons[3] = {
    {kBox3ButtonConfirmGpio, "confirm", false, false, 0},
    {kBox3ButtonDenyGpioUnverified, "deny", false, false, 0},
    {kBox3ButtonStopGpioUnverified, "stop", false, false, 0},
};

static void setupButtons() {
  for (auto& b : gBox3Buttons) {
    pinMode(b.pin, INPUT_PULLUP);
    b.raw = b.stable = (digitalRead(b.pin) == LOW);
    b.changedMs = millis();
  }
}

static void pollButtons() {
  const uint32_t now = millis();
  for (auto& b : gBox3Buttons) {
    const bool sample = (digitalRead(b.pin) == LOW);
    if (sample != b.raw) {
      b.raw = sample;
      b.changedMs = now;
    }
    if (b.raw != b.stable && now - b.changedMs >= kBtnDebounceMs) {
      b.stable = b.raw;
      if (b.stable) emitTouchControl(b.control);  // fire on press only
    }
  }
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
  if (strcmp(destination, kSelfId) != 0 || strcmp(source, "mac_orchestrator") != 0) {
    sendError(traceId, sequence, messageId, "mac_must_be_endpoint");
    return;
  }
  if (strcmp(transport, "usb_serial") != 0) {
    sendError(traceId, sequence, messageId, "transport_unsupported");
    return;
  }

  if (strcmp(type, "health") == 0) {
    sendAck(traceId, sequence, messageId, "ok");
    return;
  }
  if (strcmp(type, "display.render") == 0) {
    JsonObject payload = doc["payload"].as<JsonObject>();
    const char* title = payload["title"] | "";
    const char* body = payload["body"] | "";
    if (payload.isNull() || strlen(title) > 80 || strlen(body) > 180) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    // TODO(hardware): draw title/body/branches on the panel once a BSP is chosen.
    sendAck(traceId, sequence, messageId, "not_drawn");
    return;
  }
  if (strcmp(type, "speech.render") == 0) {
    JsonObject payload = doc["payload"].as<JsonObject>();
    const char* text = payload["text"] | "";
    if (payload.isNull() || !text[0] || strlen(text) > 280) {
      sendError(traceId, sequence, messageId, "invalid_payload");
      return;
    }
    // TODO(hardware): speak via the speaker codec once a driver exists.
    sendAck(traceId, sequence, messageId, "not_spoken");
    return;
  }
  sendError(traceId, sequence, messageId, "invalid_payload");
}

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
      gOverflow = true;
      continue;
    }
    gLine[gLen++] = static_cast<char>(c);
  }
}

void setup() {
  Serial.begin(115200);
  setupButtons();
  delay(200);
  JsonDocument payload;
  payload["board"] = "ESP32-S3-BOX-3";
  payload["firmware"] = kFirmwareName;
  payload["status"] = "ready";
  payload["scaffold"] = true;
  payload["display_driver"] = false;
  payload["touch_driver"] = false;
  payload["mic_driver"] = false;
  payload["speaker_driver"] = false;
  // Physical buttons -> touch.event control. The pins are UNVERIFIED except
  // GPIO0 (BOOT) for confirm. Not a claim that all three are wired correctly.
  payload["buttons"] = true;
  sendMessage("health", "boot", 0, payload.as<JsonObject>());
}

void loop() {
  pumpSerial();
  pollButtons();
}
