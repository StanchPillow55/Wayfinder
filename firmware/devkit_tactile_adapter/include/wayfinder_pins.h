// Wayfinder DevKitC-1 bench pin map. SOURCE ONLY. EVERY PIN HERE IS UNVERIFIED.
//
// Nothing below has been traced on a real board, wired, or measured. These are
// a suggested starting map for a person with a breadboard (docs/hardware/breadboard.md).
// Change a number here and in that doc together, and log the measurement in
// docs/hardware/sprint.md before writing "verified" anywhere.
//
// This header changes no protocol behavior. It only names numbers. src/main.cpp
// reads the three button pins (INPUT_PULLUP, active low, pressed = pin to GND) and
// the analog pin as INPUTS ONLY, in both builds, and reports them as touch.event
// demo_input messages. The health-only build (WAYFINDER_MOTOR_ENABLE=0) never
// configures the motor signal pin, kBenchSignalGpioUnverified. Nothing here has been
// compiled, flashed, or wired.
//
// The motor signal pin is defined in src/main.cpp as kServoPin (the name is kept
// because tests grep for it) and must equal kBenchSignalGpioUnverified below. The
// static_assert in main.cpp fails the build if the two drift apart.
//
// Never power the motor from the DevKit 5V or 3V3 pin. External supply, common ground.
//
// Pins to avoid on a DevKitC-1 (from docs/hardware/pinouts.md and Espressif's pinout,
// not re-checked against your board):
//   0, 3, 45, 46   strapping pins
//   19, 20         native USB D-/D+
//   43, 44         UART0 console (the USB serial link to the Mac on some boards)
//   26..32         module flash
//   33..37         octal flash/PSRAM on some modules (N8R8)
//   48             on-board RGB LED on some board revisions
#pragma once

// UNVERIFIED: motor driver / ESC signal output.
static constexpr int kBenchSignalGpioUnverified = 18;

// UNVERIFIED: spare digital inputs for three momentary buttons (to ground, internal pull-up).
[[maybe_unused]] static constexpr int kBenchButton1GpioUnverified = 4;
[[maybe_unused]] static constexpr int kBenchButton2GpioUnverified = 6;
[[maybe_unused]] static constexpr int kBenchButton3GpioUnverified = 7;

// UNVERIFIED: one analog input (ADC1, 3.3 V full scale at most). Never feed it more than 3.3 V.
[[maybe_unused]] static constexpr int kBenchAnalogGpioUnverified = 1;
