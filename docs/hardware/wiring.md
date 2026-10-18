# Actuator wiring (130 mini DC brushless motor)

The actuator on hand is a 130 mini DC brushless motor. It spins and does not hold an angle. ADR-004 records the change from a servo.

**Nothing here is verified.** The motor's driver or ESC, supply voltage, stall current, and signal type are unknown. This file states a topology to check against the real parts. It is not a build guide, and the software never energizes anything. Do not power or connect anything from this page alone.

```
USB (UART)                          power source (voltage unknown)
    |                                   |
ESP32-S3 DevKitC-1                  breadboard rail
pin labeled 18 ---- signal ----->   driver or ESC signal input   (GPIO 18 is an unverified assumption)
pin labeled GND ---> ground rail ---> driver ground ---> source negative
                                     driver power <--- source positive (after the rating is checked)
                                     motor plugs into the driver output
```

Open questions the bench must answer before any power:

- Does this motor need an ESC, a driver board, or something else? A brushless motor is not driven by a bare GPIO.
- What voltage and current does it need, and what does the supply provide?
- Which driver pin takes the control signal, and is it a duty cycle, a servo-style pulse, or something else?
- Is GPIO 18 a safe output on this DevKit header?

Do not connect any external supply to the DevKit pin labeled 5V or 3V3.

## Jumper genders

DevKit headers are male.

- Female-to-male: DevKit pin to a breadboard row. Female end on the DevKit.
- Male-to-male: breadboard row to a driver pin that is a female socket, or a rail hop.
- Female-to-female: male pin to male pin when a part does not sit in the breadboard.
- Grove cable, Seeed order, if the driver side is a Grove socket: yellow signal, white unused, red power on the driver side only, black ground. Do not land the Grove red wire on the DevKit.

## Power-on order (once everything above is verified)

1. Common ground between supply, driver, and DevKit.
2. Control signal from the DevKit pin to the driver signal input, not to a motor-output terminal.
3. Supply.
4. USB to the Mac.

If the DevKit resets when the motor runs, disable the actuator in Wayfinder and keep using the on-screen tactile view. Do not retry by powering the motor from the board.

## Cables

- DevKit with Micro-USB: USB-C-to-Micro-USB data cable.
- DevKit v1.1: USB-C data cable in the UART socket, not only the native USB socket.
- BOX-3: USB-C data cable into the main unit, not the dock's power-only USB-C.
- P4X-EYE: USB-C data cable into the port marked Debug.

The MLH kit has one USB-C data cable. BOX-3 and P4X-EYE cannot both be attached beside a USB-C DevKit unless the laptop already has another data cable. The software keeps working with any of them unplugged.
