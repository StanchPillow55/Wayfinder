// Wayfinder ESP32-S3-BOX-3 button pin map. SOURCE ONLY. PINS ARE UNVERIFIED.
//
// Three momentary buttons -> touch.event {"control": ...}. Input only, active
// low, internal pull-up (pressed = pin reads LOW). No panel, audio, touch, or
// ADC driver is used. The Mac already accepts {"control":"confirm"|"deny"|
// "stop"} as a touch.event from any board (see src/wayfinder/models.py
// _validate_payload and orchestrator._ingest), so no Mac-side change is needed.
//
// Pin confidence (from the official ESP32-S3-BOX-3 pinout / esp-bsp):
//   GPIO0  BOOT button. A real dedicated digital input with a pull-up. This is
//          the one button we are confident fires on day one. Mapped to confirm.
//          NOTE: GPIO0 is a strapping pin. It must be HIGH at reset (its
//          default). Only ever read it AFTER boot, never drive it. Holding it
//          at power-on enters download mode; that is normal and expected.
//   GPIO47, GPIO48  UNVERIFIED candidates for the two top-edge buttons. On the
//          BOX-3 the top buttons may sit on an ADC ladder rather than dedicated
//          GPIOs; if these two never register a press at the bench, that is why,
//          and it is harmless (input-only). Confirm by pressing and watching the
//          event stream, then record the working numbers in docs/hardware/sprint.md.
//
// Change a number here and update docs/hardware/sprint.md with the measurement
// before writing "verified" anywhere.
#pragma once

// Confident: BOOT button, dedicated digital input, active low.
static constexpr int kBox3ButtonConfirmGpio = 0;    // GPIO0 BOOT -> confirm

// UNVERIFIED: top-edge buttons. Harmless if wrong (input-only).
static constexpr int kBox3ButtonDenyGpioUnverified = 47;   // -> deny
static constexpr int kBox3ButtonStopGpioUnverified = 48;   // -> stop
