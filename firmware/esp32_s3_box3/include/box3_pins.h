// Wayfinder ESP32-S3-BOX-3 button pin map. SOURCE ONLY.
//
// Buttons -> touch.event {"control": ...}. Input only, active low, internal
// pull-up (pressed = pin reads LOW). The Mac already accepts
// {"control":"confirm"|"deny"|"stop"} as a touch.event from any board
// (src/wayfinder/models.py _validate_payload, orchestrator._ingest), so no
// Mac-side change is needed.
//
// VERIFIED AT THE BENCH (2026-10-18, this board, over USB-Serial/JTAG):
//   GPIO1  Usable physical input. Clean active-low press/release pairs, no USB
//          disruption. Mapped to "confirm". (Schematic: speaker mute switch.)
//   GPIO0  MCU-BOOT / strapping pin. Reads as a button, BUT every press DROPS
//          the native-USB link and reboots the board (observed: reconnect +
//          health reset on each press). Watched for diagnostics only; it must
//          NOT trigger a control. Do not map an action to GPIO0.
//
// Pin facts from the official schematic (do NOT guess these):
//   GPIO47 LCD BACKLIGHT control  -- NOT a button. Never touch.
//   GPIO48 LCD RESET              -- NOT a button. Never touch.
//   The three top "functional" buttons are an ADC button ladder, not plain
//   digital GPIOs; only GPIO0 (unusable) and GPIO1 came through as digital.
//
// DISCOVERY MODE (WAYFINDER_BOX3_PIN_PROBE): emits a diagnostic touch.event
// {"debug_pin":<gpio>,"level":0|1} on any watched-pin edge. OFF by default now
// that GPIO1 is confirmed; set to 1 to re-run pin discovery at the bench.
#pragma once

#ifndef WAYFINDER_BOX3_PIN_PROBE
#define WAYFINDER_BOX3_PIN_PROBE 0
#endif

// Safe dedicated digital inputs to watch. GPIO47/48 are LCD lines (excluded).
// GPIO0 is watched so discovery can see it, but it is never mapped to a control.
static constexpr int kBox3ProbePins[] = {0, 1};

// Confirmed control button. GPIO1 is clean and does not disturb USB.
static constexpr int kBox3ButtonConfirmGpio = 1;    // GPIO1 -> confirm

// Explicitly NOT a control: pressing it resets the board over native USB.
static constexpr int kBox3UsbResettingPin = 0;      // GPIO0 BOOT/strapping
