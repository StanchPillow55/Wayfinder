# Pin assumptions

Verified means we have it from the proposal, the kit list, or the vendor's public board docs. Unverified means it still needs the physical board.

| Item | Status | Notes |
| --- | --- | --- |
| Mac is the only orchestrator | Verified | ADR-001. There is no existing ESP32 pair. |
| DevKitC-1 is the actuator controller | Verified | One board. No second DevKit. The actuator is a 130 mini DC brushless motor (ADR-004). |
| BOX-3 is display, touch, mics, speaker | Verified from Espressif's BOX-3 guide | USB-C on the main unit is power and data. The dock's other USB-C is power only. |
| P4X-EYE is a camera board | Verified from Espressif's P4-EYE guide | USB port marked Debug is for flash and serial. No LiDAR assumption. |
| Control signal GPIO | Unverified | Software and firmware use GPIO 18. It is a normal header pin, not a strapping pin and not USB. Confirm the silkscreen before the demo. |
| GPIO 43 and 44 | Verified as the UART console on DevKitC-1 | Do not use them for the actuator. They are the USB serial link to the Mac. |
| Avoid GPIO 0, 3, 19, 20, 45, 46 | Verified from the DevKitC-1 pinout | Strapping pins and the native USB pair. |
| DevKit USB connector | Unverified | Micro-USB on earlier boards, two USB-C sockets on v1.1. The kit has both a USB-C cable and a USB-C-to-Micro-USB cable. |
| Driver input style | Unverified | The unflashed firmware source sends a 50 Hz ESC-style pulse as a hint. Whether the motor needs an ESC, a PWM driver, or an I2C hat is unknown. If it is not a pulse or duty signal, stop and write an ADR before changing the protocol. |
| Motor supply | Unknown | Voltage and current are not known. The kit includes a 5 V pack and a driver, and neither is verified for this motor. Do not feed the motor from the DevKit rail. |

Safe alternate signal pins if GPIO 18 is occupied on the specific header: GPIO 17 or GPIO 5. Change the firmware constant and this file together.
