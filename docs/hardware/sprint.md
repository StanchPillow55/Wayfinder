# Three-hour bench sprint

Goal: see the real state of each board on the Mac, flash health-only firmware, and get a typed ACK. Nothing in this runbook proves a motor works. Do not mark GPIO 18, the driver, the motor supply, or any wiring as verified until you have measured it and written the measurement down in the log at the bottom.

**Step 1 is `scripts/hardware/bringup.sh`** (see [../../scripts/hardware/README.md](../../scripts/hardware/README.md)), not a scavenger hunt. Nothing has been flashed in this repo's test run: PlatformIO and ESP-IDF were not installed when the script was written, and every result below is still to be earned at the bench.

Read [breadboard.md](breadboard.md) (the physical interconnection), [wiring.md](wiring.md), and [pinouts.md](pinouts.md) first. They state topology and open questions. Both say nothing is verified. Protocol: [device_protocol.md](device_protocol.md). Firmware: [../../firmware/README.md](../../firmware/README.md).

Rules for the whole sprint:

- The Mac is the orchestrator (ADR-001). Boards are plugged into the Mac only. There is no UART, no ESP32-C3 display, and no board-to-board link.
- Never power the motor from the DevKit `5V` or `3V3` pin. External supply, shared ground.
- Do not hardcode a `/dev/cu.*` path anywhere. Record what you see. `bringup.sh` writes the gitignored `.wayfinder-local/bench.env` only after a typed health ACK, and you `source` it.
- A simulator result, a loopback result, and a health ACK are not a motor run.

Preflight, once, before the clock starts:

```bash
cd /Users/bradleyharaguchi/Wayfinder
source .venv/bin/activate
scripts/hardware/bringup.sh        # lists ports and tools; prints the exact install line for anything missing
```

If it reports PlatformIO or pyserial missing, run the install line it printed (`pip install platformio`, `pip install -e '.[serial]'`) and re-run it.

## Checklist

### 0:00 to 0:20. Enumerate USB (no flashing)

1. [ ] Unplug every board. `scripts/hardware/bringup.sh --snapshot` records the baseline.
2. [ ] Plug in one board with a data cable (many cables are charge-only). Wait 3 seconds. `scripts/hardware/bringup.sh --diff` prints the new port. It will not say which board it is. Repeat per board, one at a time:
   - [ ] DevKitC-1. Micro-USB board: USB-C-to-Micro-USB data cable. v1.1 with two USB-C sockets: the socket marked UART.
   - [ ] BOX-3 main unit USB-C (not the dock's power-only USB-C).
   - [ ] P4X-EYE port marked Debug.
3. [ ] Write the observed paths, vendor id, and which cable and socket in the log below. The kit has one USB-C data cable, so you may only get one board at a time.
4. [ ] If no port appears: another cable, another socket, then boot mode (hold BOOT while plugging in). Stop after three tries and log it.

`python -m wayfinder.devices --json > /tmp/wayfinder-ports.json` keeps a machine-readable copy. A vendor id is a hint about a bridge chip. It does not identify the board.

### 0:20 to 0:50. Flash health-only firmware, one board

Start with the DevKit. It is the only board with motor-related firmware, and its default build never touches the signal pin.

1. [ ] `scripts/hardware/bringup.sh --devkit-port <path from your log>`. It flashes only `devkit_health_only` and then runs the health probe (the next block). If PlatformIO is missing it prints the install command and stops before flashing.
2. [ ] If it cannot connect: hold BOOT, tap RESET, release BOOT, retry the same command.
3. [ ] Optional, after the probe: `source .wayfinder-local/bench.env && pio device monitor -p "$WAYFINDER_DEVKIT_PORT"`. Expect one boot `health` line with `"motor_enabled_build":false`. Press RESET to see it again. Close the monitor before the next step (only one program may hold the port).
4. [ ] Log the exact `pio` output and any workaround.

If it does not compile against the real core, fix the source in `firmware/` and log what changed. The source has only been syntax-checked on a host with stubs.

BOX-3 and P4X-EYE firmware is a scaffold. Flashing it proves the envelope and nothing else. Do it only after the DevKit, and only if time remains. Procedures are in `firmware/esp32_s3_box3/README.md` and `firmware/p4_eye/README.md`; several steps there are marked unverified.

### 0:50 to 1:10. Typed ACK, health only

1. [ ] `bringup.sh --devkit-port ...` already ran this probe. To repeat it alone: `scripts/hardware/bringup.sh --probe devkit_tactile --port <path>` (same as `python -m wayfinder.devices --probe devkit_tactile --port <path>`).
2. [ ] Pass criterion: `Typed health ACK from devkit_tactile ... health ACK only. The motor has not been commanded.` The probe sends `health` and nothing else, so it cannot command the motor.
3. [ ] If there is no ACK: opening the port may have reset the board. Retry once. A port that does not answer is not that board. Do not export the variable for it.
4. [ ] `source .wayfinder-local/bench.env`, then start the app with the variable set and read the status: `export WAYFINDER_DEVKIT_PORT=...; wayfinder`, then open the UI. Status `usb` means the port opened. It does not mean ACKed. Press the Ping DevKit button; the detail text should say the board acknowledged health on USB and that this was not a motor run.
5. [ ] Repeat `bringup.sh --probe <board> --port <path>` for any other board you flashed by hand (`esp32_s3_box3`, `p4_eye`). The script prints those flash commands after the DevKit ACK.

After a health ACK, the same firmware can also report button and analog events as `touch.event` (`demo_input` next, back, analog level 0 to 100, plus `control` confirm and stop) if you wire the buttons and pot from [breadboard.md](breadboard.md). That is UNVERIFIED source, not flashed or bench-tested, and needs no motor wiring. It is not a motor run: motor motion is still a separate `--motor-bench` step, after the meter checks below.

This is the first moment a physical typed ACK exists, and only for the board that answered. Record the date, port, and firmware string the probe printed.

### 1:10 to 1:25. Software-path checks with the motor still unwired

Both of these use the health-only build, so no motor output exists.

1. [ ] Send a render through the app (any step). Expected ack status `motor_disabled`, shown by the app as unacknowledged with the on-screen tactile fallback. That proves the render path, bounds, and fallback.
2. [ ] Send the same sequence twice, and two renders within 50 ms, and confirm `duplicate_sequence` and `rate_limited` appear as the ack `status`. Only one program can hold the port: either read the app's tactile text, or stop the app and use `pio device monitor`.
3. [ ] Press Disable actuator in the app. The firmware should report `lockout: true` on its next ack. Enable it again and confirm `lockout: false`. Detach (a plain `safe_disable`) is separate: it does not latch.

### 1:25 to 2:15. Meter checks before any power

Nothing is energized in this block except USB to the Mac. Write every number down. If you cannot fill in a value, stop.

1. [ ] Read the markings on the motor, the driver or ESC, and the supply. Photograph them. Record the part names exactly as printed. Do not infer ratings from the motor's size.
2. [ ] Decide what the driver is: an ESC, a PWM motor driver, or something else. If you cannot tell from the markings or a datasheet you have in hand, **stop**. Wiring.md lists this as an open question.
3. [ ] Find the driver's signal input pin and its logic-level requirement from the datasheet. Record it. Do not assume it takes a 3.3 V logic signal.
4. [ ] With the meter, set to continuity, USB unplugged: confirm the DevKit pin you plan to use for the signal is the header position you think it is, by tracing silkscreen to the pin. GPIO 18 is an unverified assumption. If the label differs, change `kServoPin` in the firmware and `pinouts.md` together. Avoid GPIO 0, 3, 19, 20, 43, 44, 45, 46.
5. [ ] With the meter on resistance, supply disconnected: check the driver's power input is not shorted to ground, and that ground on the supply, driver, and DevKit would be common once wired.
6. [ ] Meter on DC volts, supply on, **nothing else connected**: record the supply's actual output voltage. Compare against the driver's and motor's printed ratings. Higher than either rating is a stop.
7. [ ] Confirm the supply's current rating is at least what the motor or driver documents as its need. If no figure exists, stop.
8. [ ] Wire per wiring.md with everything off: ground common first, then signal to the driver's signal input (not a motor output terminal), then driver power. Leave the supply off and the motor shaft clear of anything.
9. [ ] With the supply still off, re-check with the meter: no continuity from the supply positive to the DevKit `5V`, `3V3`, or the signal pin.

### 2:15 to 2:45. Motor bench build, only if every box above is ticked

Do not enter this block with an unticked box, an unknown rating, or an unidentified driver.

1. [ ] A person other than the one at the keyboard (or you, deliberately) holds the power switch or has the supply's disconnect in reach. Secure the motor. Keep hands, hair, and cables away from the shaft.
2. [ ] Flash the motor build with the supply **off**: `scripts/hardware/bringup.sh --devkit-port <same path> --motor-bench`. It refuses unless a typed health ACK is already recorded for that exact port, and it asks you to type `supply off`. It runs the health probe afterward.
3. [ ] If you need to repeat the probe: `scripts/hardware/bringup.sh --probe devkit_tactile --port <same path>`. A typed health ACK from this build must come first, with `motor_enabled_build: true` in the monitor's boot line. Health never runs the output.
4. [ ] Typed ACK before any motion, as a rule: the step above must pass in this session on this build. If the ACK is missing, stop.
5. [ ] Only then switch the supply on. Send the smallest frozen pose, `orient` (30% duty, 180 ms, forward), from the app. Have the supply switch in hand.
6. [ ] Stop at once if anything runs longer than the command, gets hot or smells, the board resets, or the shaft does something you did not command. Press Disable actuator and cut the supply.
7. [ ] Log exactly what happened. An ACK with `output_driven: true` means the firmware drove the pin. Only you can observe the motor. Write down what you saw. Do not log "verified" for an ACK alone.
8. [ ] `reverse` (the `failure` pose) answers `reverse_unsupported` and does nothing. That is intentional until a direction scheme is chosen. Write an ADR before changing the protocol.

Then flip to supply off and unplug before touching wiring.

### 2:45 to 3:00. Wrap up

1. [ ] Supply off. Motor build is a bench build; re-flash `devkit_health_only` if the board will be left unattended.
2. [ ] Fill in the log below. Update `pinouts.md` rows only for values you measured, and say how.
3. [ ] Note which items stayed unverified.

## Stop conditions

Stop and cut power when any of these is true:

- No data port appears after three cable or socket changes.
- A port answers but not as the board you expect (`wrong_board`).
- The driver, supply voltage, or current requirement is unknown, or the supply exceeds a printed rating.
- Any step would connect an external supply to the DevKit `5V` or `3V3` pin.
- The board resets, browns out, or gets warm when the supply is switched on.
- The motor runs longer than the commanded duration, or runs without a command.
- You catch yourself writing "verified" for something you did not measure or see.

## What counts as proven

| Claim | Evidence needed | Status |
| --- | --- | --- |
| A DevKit is on USB and answers | `--probe devkit_tactile` typed ACK, logged | Not yet |
| Firmware compiles for the real core | `pio run` output, logged | Not yet |
| The render path and bounds work on the board | Monitor and ack statuses, logged | Not yet |
| GPIO 18 is a usable signal pin | Silkscreen and meter trace, logged | Not yet |
| The driver type and supply are compatible | Part markings, datasheet, meter readings, logged | Not yet |
| The motor ran on command | A human observed it, logged, after a typed ACK | Not yet |
| BOX-3 or P4X-EYE run application firmware | Not possible with the scaffolds. Needs panel, audio, and camera drivers. | Not yet |

## Bench log

Copy this block per board. Fill it in at the bench, not before.

```
Board:
Date:
Cable / socket:
Port seen (not hardcoded anywhere):
USB vendor:product:
pio / idf output (paste or link):
Probe result:
Notes:
```
