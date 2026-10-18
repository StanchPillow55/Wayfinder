# ADR-001: Mac-orchestrated USB topology

- Status: Accepted
- Date: 2026-10-17
- Proposal: Wayfinder Hardware Integrated Project Proposal 2.0
- Supersedes for this repository: D-003, D-004, D-005, HW-002, HW-003, HW-004

> Note (ADR-004, 2026-10-18): the actuator on hand is a 130 mini DC brushless motor, not a servo. Read "servo" below as "actuator". The topology is unchanged.

## Context

Proposal 2.0 says the hardware MVP is an existing ESP32-S3 capture board linked by UART to an ESP32-C3 display board. The C3 is the rendering node. UART (TX, RX, common ground, 3.3 V rails not tied) is the primary local link. The DevKitC-1 plus one actuator is described as a later physical-output reach. BOX-3 and P4X-EYE are optional surfaces after that pair works.

That pair is not hardware we have. There is no existing ESP32 in this project. The Mac is the orchestrator. The boards on hand are:

- 1× ESP32-S3 DevKitC-1
- 1× ESP32-S3-BOX-3
- 1× P4X-EYE

The MLH kit for the servo path is:

- 1 servo motor
- 1 motor/servo driver board
- 1 Grove jumper cable
- 1 set female-to-female jumpers
- 1 set female-to-male jumpers
- 1 set male-to-male jumpers
- 1 small breadboard
- 1 suitable 5 V power source / battery pack
- 1 USB-C data cable
- 1 USB-C-to-Micro-USB data cable or adapter

The proposal's own rule is to record this kind of conflict as an ADR and to keep the product principles: the Mac owns committed WIR, devices are adapters, the core demo survives a missing board, and a servo is not a Braille display.

## Decision

The Mac is the only orchestrator. Boards do not address each other. There is no UART link and no C3 node.

```
                         WAYFINDER / MAC
                  canonical WIR + orchestration
                             |
           +-----------------+-----------------+
           |                 |                 |
        USB serial        USB serial        USB debug
           |                 |                 |
    S3 DevKitC-1        S3-BOX-3           P4X-EYE
    physical output     human I/O           perception
           |            display / touch      camera
    servo driver        voice / speaker      local events
           |
         servo
```

- DevKitC-1 is physical output only. It drives one servo through the driver board and the external 5 V pack.
- BOX-3 is the human I/O surface. It takes the display and touch role the proposal gave the C3, and it can also carry speech. The software demo does not require it.
- P4X-EYE emits semantic perception events only. It is not a LiDAR or time-of-flight sensor. The software demo does not require it.
- The device envelope stays protocol 0.1. `transport` is `usb_serial`, `usb_debug`, or `simulator`. `uart` is rejected in code.
- Messages whose source or destination is `esp32_c3_display` or `esp32_s3_gateway`, and any board-to-board message, are rejected.
- The Mac UI and the on-screen tactile renderer stay available when a board is unplugged. Unplugging a board does not erase WIR.

## Consequences

D-001, D-002, D-006, D-008, D-009, and D-010 still hold. D-006 still means one servo is a physical-output proof, not a refreshable Braille device. D-007 is narrowed: BOX-3 and P4X-EYE are the two optional boards in this topology, not follow-on boards behind an S3/C3 pair.

HW-001 is met by BOX-3 speech and touch events and by P4X-EYE vision events, all typed, with raw media refused. HW-005, HW-006, and HW-008 are the DevKit servo path plus the software fallback and the command bounds. HW-007 is the P4X-EYE event path.

## Assumptions still unverified

- DevKitC-1 USB generation: one Micro-USB socket, or two USB-C sockets on v1.1
- Servo signal on GPIO 18
- Whether the driver board wants a PWM signal or an I2C servo hat
- Which USB bridge name appears in `/dev/cu.*`
- BOX-3 display/microphone firmware and P4X-EYE vision firmware, which are not in this repo
- Command-to-servo latency on the bench (the software target remains under 250 ms after a committed render; it has not been measured)
- Cable count: one USB-C data cable cannot attach BOX-3 and P4X-EYE at the same time as a USB-C DevKit

## What this ADR refuses

- An `esp32_c3_display` firmware target
- Servo power taken from the DevKit 5 V or 3 V3 pin
- A board choosing a browser action
- Storing raw camera or microphone samples
- Treating the simulator acknowledgement as a physical servo move
