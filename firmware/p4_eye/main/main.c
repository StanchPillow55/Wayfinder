/*
 * Wayfinder P4X-EYE adapter SCAFFOLD.
 *
 * STATUS: SOURCE ONLY. Not built, not flashed, not run on a board. Nothing here
 * proves a P4X-EYE has been seen by the Mac. The board counts as present only
 * after it enumerates as a /dev/cu.* port and
 *   python -m wayfinder.devices --probe p4_eye
 * receives a typed ACK.
 *
 * What this does: reads newline-delimited JSON from the stdio console, enforces
 * the Mac envelope (protocol 0.1, transport usb_debug, <= 1024 bytes), answers
 * health, and refuses everything else.
 *
 * What it does not do: capture from the camera, run vision, or emit events on
 * its own. emit_vision_event() exists for a future camera task and is not called.
 * Camera semantics only: no raw frames, no image, jpeg, base64, or audio fields
 * ever go into a WIR message.
 *
 * UNVERIFIED: which console (UART or USB Serial/JTAG) the Debug port carries,
 * and whether blocking reads on stdin work with the default config.
 */
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define MAX_BYTES 1024
#define SELF_ID "p4_eye"
#define FIRMWARE_NAME "p4_eye_scaffold 0.1 source-only"

static uint32_t g_out_seq = 0;

static uint32_t now_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

static void send_message(const char *type, const char *trace_id, uint32_t sequence, cJSON *payload) {
  char message_id[24];
  snprintf(message_id, sizeof(message_id), "p4%lu", (unsigned long)g_out_seq++);
  cJSON *doc = cJSON_CreateObject();
  cJSON_AddStringToObject(doc, "protocol_version", "0.1");
  cJSON_AddStringToObject(doc, "transport", "usb_debug");
  cJSON_AddStringToObject(doc, "trace_id", trace_id);
  cJSON_AddStringToObject(doc, "message_id", message_id);
  cJSON_AddNumberToObject(doc, "sequence", sequence);
  cJSON_AddStringToObject(doc, "source", SELF_ID);
  cJSON_AddStringToObject(doc, "destination", "mac_orchestrator");
  cJSON_AddStringToObject(doc, "type", type);
  cJSON_AddNumberToObject(doc, "created_at_ms", now_ms());
  cJSON_AddItemToObject(doc, "payload", payload);
  cJSON_AddBoolToObject(doc, "requires_ack", false);
  char *text = cJSON_PrintUnformatted(doc);
  if (text) {
    fputs(text, stdout);
    fputc('\n', stdout);
    fflush(stdout);
    cJSON_free(text);
  }
  cJSON_Delete(doc);
}

static void send_ack(const char *trace_id, uint32_t sequence, const char *ack_id, const char *status) {
  cJSON *payload = cJSON_CreateObject();
  cJSON_AddStringToObject(payload, "ack_message_id", ack_id);
  cJSON_AddStringToObject(payload, "status", status);
  cJSON_AddStringToObject(payload, "state_token", "cleared");
  cJSON_AddBoolToObject(payload, "camera_driver", false);
  send_message("ack", trace_id, sequence, payload);
}

static void send_error(const char *trace_id, uint32_t sequence, const char *ack_id, const char *code) {
  cJSON *payload = cJSON_CreateObject();
  cJSON_AddStringToObject(payload, "code", code);
  if (ack_id && ack_id[0]) cJSON_AddStringToObject(payload, "ack_message_id", ack_id);
  send_message("error", trace_id, sequence, payload);
}

/* vision.event: event is user_present, scene_changed, or scene_summary. summary is
 * optional, at most 200 characters, untrusted text. Never an image or frame. Not called yet. */
__attribute__((unused)) static void emit_vision_event(const char *event, const char *summary) {
  if (!event) return;
  if (strcmp(event, "user_present") != 0 && strcmp(event, "scene_changed") != 0 &&
      strcmp(event, "scene_summary") != 0)
    return;
  if (summary && strlen(summary) > 200) return;
  cJSON *payload = cJSON_CreateObject();
  cJSON_AddStringToObject(payload, "event", event);
  if (summary && summary[0]) cJSON_AddStringToObject(payload, "summary", summary);
  send_message("vision.event", "vision", 0, payload);
}

static const char *str_of(const cJSON *obj, const char *key) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
  return (cJSON_IsString(item) && item->valuestring) ? item->valuestring : "";
}

static void handle_line(const char *line) {
  cJSON *doc = cJSON_Parse(line);
  if (!doc || !cJSON_IsObject(doc)) {
    send_error("none", 0, "", "invalid_message");
    cJSON_Delete(doc);
    return;
  }
  const char *transport = str_of(doc, "transport");
  const char *source = str_of(doc, "source");
  const char *destination = str_of(doc, "destination");
  const char *type = str_of(doc, "type");
  const char *trace_id = str_of(doc, "trace_id");
  const char *message_id = str_of(doc, "message_id");
  const cJSON *seq_item = cJSON_GetObjectItemCaseSensitive(doc, "sequence");
  uint32_t sequence = cJSON_IsNumber(seq_item) && seq_item->valueint >= 0 ? (uint32_t)seq_item->valueint : 0;
  if (!trace_id[0]) trace_id = "none";

  if (strcmp(transport, "uart") == 0) {
    send_error(trace_id, sequence, message_id, "uart_topology_retired");
  } else if (strcmp(str_of(doc, "protocol_version"), "0.1") != 0) {
    send_error(trace_id, sequence, message_id, "protocol_version");
  } else if (strcmp(source, "esp32_c3_display") == 0 || strcmp(destination, "esp32_c3_display") == 0) {
    send_error(trace_id, sequence, message_id, "retired_topology");
  } else if (strcmp(destination, SELF_ID) != 0 || strcmp(source, "mac_orchestrator") != 0) {
    send_error(trace_id, sequence, message_id, "mac_must_be_endpoint");
  } else if (strcmp(transport, "usb_debug") != 0) {
    send_error(trace_id, sequence, message_id, "transport_unsupported");
  } else if (strcmp(type, "health") == 0) {
    send_ack(trace_id, sequence, message_id, "ok");
  } else {
    send_error(trace_id, sequence, message_id, "invalid_payload");
  }
  cJSON_Delete(doc);
}

void app_main(void) {
  cJSON *payload = cJSON_CreateObject();
  cJSON_AddStringToObject(payload, "board", "P4X-EYE");
  cJSON_AddStringToObject(payload, "firmware", FIRMWARE_NAME);
  cJSON_AddStringToObject(payload, "status", "ready");
  cJSON_AddBoolToObject(payload, "scaffold", true);
  cJSON_AddBoolToObject(payload, "camera_driver", false);
  send_message("health", "boot", 0, payload);

  static char line[MAX_BYTES + 1];
  size_t len = 0;
  bool overflow = false;
  for (;;) {
    int c = fgetc(stdin);
    if (c == EOF) {
      /* No console input yet. Poll again; this is the unverified transport binding. */
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (c == '\r') continue;
    if (c == '\n') {
      if (overflow) {
        send_error("none", 0, "", "message_too_large");
      } else if (len > 0) {
        line[len] = '\0';
        handle_line(line);
      }
      len = 0;
      overflow = false;
      continue;
    }
    if (len >= MAX_BYTES) {
      overflow = true;
      continue;
    }
    line[len++] = (char)c;
  }
}
