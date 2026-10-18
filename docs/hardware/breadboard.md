# Breadboard and interconnection map

For someone who has not wired anything yet. **Nothing here is verified.** No board in this repo has been flashed, no motor has run, and no physical ACK exists. Pin numbers, the driver, and the supply are assumptions until you measure them and write the result in the bench log below and in [sprint.md](sprint.md).

Topology is fixed by ADR-001: the Mac owns the Wayfinder Interaction Representation, and every board talks only to the Mac, over its own USB serial port. There is no UART, no ESP32-C3, and no board-to-board wire for data.

## 1. Data: three separate USB-C cables, nothing through the breadboard

```
                         +--------------------------+
 USB-C data cable 1 ---- |                          | ---- ESP32-S3 DevKitC-1  (actuator, health, tactile.render)
 USB-C data cable 2 ---- |          Mac             | ---- ESP32-S3-BOX-3      (display, speech, touch)
 USB-C data cable 3 ---- |  (the only orchestrator) | ---- ESP32-P4 P4X-EYE    (vision)
                         +--------------------------+
        DevKit, BOX-3, and P4X-EYE have no wire to each other.
```

- Use a **data** cable. Many are charge-only. If no port appears, try another cable, then another socket.
- DevKit with Micro-USB: USB-C to Micro-USB data cable. DevKit v1.1 with two USB-C sockets: the socket marked UART.
- BOX-3: USB-C on the main unit, not the dock's power-only USB-C.
- P4X-EYE: the port marked Debug.
- The kit has one USB-C data cable. Use one board at a time unless you have more cables. The software keeps working with any board unplugged.
- Data never crosses the breadboard. The breadboard is for the motor path only (section 3).

### What the Mac sends and receives on each link

Protocol 0.1, newline-delimited JSON, at most 1024 bytes per message, `transport: usb_serial`. Pose tokens: `cleared`, `orient`, `branch_ready`, `needs_confirmation`, `action_verified`, `failure`. See [device_protocol.md](device_protocol.md).

| Link | Mac sends | Mac receives |
| --- | --- | --- |
| DevKit (`devkit_tactile`) | `health`, `tactile.render` (pose token plus `duty_percent`, `duration_ms`, `direction`; `angle_deg` and `pulse_us` are an unverified ESC-style hint), `safe_disable` | boot `health`, `ack` (a status like `ok`, `motor_disabled`, `rate_limited`), `error`, `touch.event` (button and analog demo inputs, source only, unverified) |
| BOX-3 (`esp32_s3_box3`) | `health`, `display.render`, `speech.render` | `ack` (scaffold answers `not_drawn` or `not_spoken`), `touch.event`, `speech.event` (nothing emits these until real drivers exist) |
| P4X-EYE (`p4_eye`) | `health` | `ack`, `vision.event` (nothing emits it until a camera driver exists) |

Rules:

1. Boards never talk to each other. Anything one board needs from another goes through the Mac.
2. A health ACK means that firmware answered a typed ping on that port. It is not a motor run, a drawn screen, a spoken sentence, or a camera frame.
3. In the health-only DevKit build, `tactile.render` is checked and answered `motor_disabled`. The signal pin is never configured.
4. The BOX-3 and P4X-EYE firmware are source-only scaffolds. They prove the envelope and nothing else.

## 2. Bring-up order

```bash
scripts/hardware/bringup.sh --snapshot       # board unplugged
scripts/hardware/bringup.sh --diff           # after plugging in one board
scripts/hardware/bringup.sh --devkit-port /dev/cu.<path>
```

See [../../scripts/hardware/README.md](../../scripts/hardware/README.md). The motor path in section 3 stays unwired until a typed health ACK has come back.

## 3. Breadboard: motor signal, ground, external supply, spare inputs

The actuator is a 130 mini DC brushless motor, not a servo. It spins and does not hold an angle. A brushless motor is not driven by a bare GPIO: it needs an ESC or a driver board. Which one you have is unknown until you read the markings.

**Power, plainly: never power the motor from the DevKit `5V` or `3V3` pin. Use an external supply with a common ground.** Only the DevKit's ground and one signal wire go to the driver side. The DevKit stays powered from USB.

```
                  external supply (voltage and current: MEASURE, unknown)
                      +  |                |  -
                         |                |
                         |                +-----------------+------------------+
                         |                                  |                  |
                  driver / ESC power in +        driver / ESC ground      breadboard ground rail
                         |                                  |                  |
 DevKit GPIO 18 (UNVERIFIED) -----> driver / ESC SIGNAL in  |        DevKit GND -+
                                          |
                                   driver / ESC output ----> motor (130 mini brushless)

 DevKit 5V and 3V3: NOT connected to the supply, the driver power, or the motor. Not ever.
```

Common ground means: external supply negative, driver ground, and a DevKit `GND` pin are joined. Signal goes to the driver's signal input, never to a motor output terminal.

### Suggested pin map. Every row is UNVERIFIED

These live in `firmware/devkit_tactile_adapter/include/wayfinder_pins.h`. The motor signal is `kServoPin = 18` in `src/main.cpp` (the name is a historical one that tests grep for). The build fails if the two disagree. `src/main.cpp` (source only, not compiled or flashed) reads the three buttons and the analog input in both builds, as inputs only, and sends them to the Mac as `touch.event` `demo_input` messages (see [device_protocol.md](device_protocol.md)). The health-only build never configures GPIO 18. Buttons are read as **active low**: the code sets `INPUT_PULLUP`, so a press must pull the pin to ground. Do not wire a button to 3V3.

| Function | DevKit GPIO | Status | Wiring suggestion |
| --- | --- | --- | --- |
| Motor driver / ESC signal (output) | 18 | UNVERIFIED | One wire to the driver's signal input. Check the driver's logic level first. Do not assume it accepts 3.3 V. |
| Button 1 (input) | 4 | UNVERIFIED | Momentary switch from the pin to ground. Firmware uses the internal pull-up (active low). Sends `demo_input: next` on press. |
| Button 2 (input) | 6 | UNVERIFIED | Same wiring. Sends `control: confirm` on press. |
| Button 3 (input) | 7 | UNVERIFIED | Same wiring. Short press sends `demo_input: back`; held 800 ms or more sends `control: stop`. |
| Analog 1 (input, ADC1) | 1 | UNVERIFIED | 3.3 V maximum. Wiper of a small pot between `3V3` and `GND` is fine; that draws no motor power. Sends `demo_input: analog` with level 0 to 100, moving a highlight on the Mac only. A floating pin reads noise. |
| Ground | `GND` | UNVERIFIED which header position you used | Join to the breadboard ground rail. |

Pins to avoid: 0, 3, 45, 46 (strapping), 19, 20 (native USB), 43, 44 (console), 26 to 32 (flash), 33 to 37 (octal flash or PSRAM on some modules), 48 (on-board LED on some revisions). If GPIO 18 is occupied on your header, GPIO 17 or 5 are the documented alternates. Change `wayfinder_pins.h`, `src/main.cpp`, [pinouts.md](pinouts.md), and this table together. Trace each pin from the silkscreen with a meter before you trust the table.

Jumper genders and the power-on order are in [wiring.md](wiring.md).

### Stop conditions

Do not apply motor power until the DevKit has returned a typed health ACK on its own USB port. Then, stop and cut the supply if any of these is true:

- The driver or ESC is unidentified, or its signal level or supply requirement is unknown.
- You have not metered the external supply with nothing else connected, or it reads above any printed rating of the driver or motor, or its current rating is unknown or too low.
- Any wire would join the external supply to the DevKit `5V` or `3V3`.
- The chip, driver, or motor gets hot, or something smells.
- The serial port disappears or the board resets when the supply is switched on.
- Current looks wrong: higher than the datasheet says, or the supply sags or current-limits.
- The motor runs longer than commanded or without a command.

### Bench log (blank; fill it in at the bench)

| Item | Expected (UNVERIFIED) | Measured / observed | Tool and date | Pass? |
| --- | --- | --- | --- | --- |
| Port path seen after plug-in | `/dev/cu.*` |  |  |  |
| Typed health ACK (`--probe devkit_tactile`) | `Typed health ACK` line |  |  |  |
| Driver / ESC part name | read from markings |  |  |  |
| Driver signal level and pin | datasheet |  |  |  |
| External supply voltage, no load | below every printed rating |  |  |  |
| Supply current rating | at least the documented need |  |  |  |
| Continuity: supply + to DevKit 5V, 3V3, signal pin | none |  |  |  |
| Signal pin on header (GPIO 18) | silkscreen 18 |  |  |  |
| Common ground continuity | yes |  |  |  |
| Button 1, 2, 3 pins (4, 6, 7) | silkscreen match |  |  |  |
| Analog pin (1) reads sensible volts | 0 to 3.3 V |  |  |  |
| Current when running | per datasheet |  |  |  |
| Motor behavior on `orient` | as observed by a human |  |  |  |
