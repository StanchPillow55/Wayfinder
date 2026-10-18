# scripts/hardware/bringup.sh

One Mac entry point for the bench sprint. It finds ports, flashes the health-only DevKit firmware to a port **you** name, and sends a typed health probe. It has not flashed anything in this repo's test runs: PlatformIO and ESP-IDF were not installed when this was written.

Run it from the repo root. Cable first: a **USB-C data** cable (many are charge-only). Each board goes to the Mac on its own cable. Boards never connect to each other. See [../../docs/hardware/breadboard.md](../../docs/hardware/breadboard.md).

## Once

```bash
cd /Users/bradleyharaguchi/Wayfinder
scripts/hardware/bringup.sh            # shows what is missing and prints the exact install commands
```

If it reports a missing tool, copy the printed line. For reference, they are:

```bash
.venv/bin/python -m pip install -e '.[serial]'     # pyserial, needed for any real port
.venv/bin/python -m pip install platformio
```

## After plugging in one USB-C cable

```bash
scripts/hardware/bringup.sh --snapshot                 # with the board UNPLUGGED
# plug in the DevKit, wait 3 seconds
scripts/hardware/bringup.sh --diff                     # prints the one new port
scripts/hardware/bringup.sh --devkit-port /dev/cu.<the path --diff printed>
```

That flashes `devkit_health_only`, then runs `python -m wayfinder.devices --probe devkit_tactile --port <port>`. On a typed ACK it writes `.wayfinder-local/bench.env` and tells you to `source` it. Then it prints the BOX-3 and P4X-EYE next commands, including the ESP-IDF install line if `idf.py` is missing.

## What it will and will not do

- **Never guesses a port.** `--diff` prints new ports and suggests a command. It does not run it. It refuses `/dev/cu.Bluetooth-Incoming-Port` and `/dev/cu.debug-console`, and refuses a port that does not exist. Bluetooth headphones show up as `/dev/cu.*` entries. They are in your snapshot, so `--diff` ignores them.
- **PlatformIO or pyserial missing:** prints the exact install command and exits 3 **before** any flash.
- **Flashes only `devkit_health_only`.** This build never configures the motor signal pin.
- **`--motor-bench` is opt-in and gated.** It needs `--devkit-port` too. It also needs a typed health ACK already recorded for that exact port in `.wayfinder-local/bench.env`, and you must type `supply off` at a prompt. It flashes the build that drives GPIO 18 (UNVERIFIED). It never energizes anything and never sends `tactile.render`.
- **BOX-3 and P4X-EYE** are not flashed by this script. It prints their commands. `--probe BOARD --port PORT` sends a health ping to either after you flash it by hand.
- **`--dry-run`** prints what would run and skips the flash, the probe, and the port-exists check. It proves nothing about hardware.

## Local files

`.wayfinder-local/` is gitignored. It holds `ports.snapshot` and `bench.env` (`export WAYFINDER_DEVKIT_PORT=...` and friends, written only after a typed ACK). Do not commit it. Set `WAYFINDER_BRINGUP_DIR` to use another directory.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (for a flash: pio ran and a typed health ACK came back) |
| 2 | Refused: bad or missing port, missing gate, bad arguments |
| 3 | A tool is missing. Nothing was flashed |
| 4 | `pio` reported a flash failure |
| 5 | No typed ACK, or the port vanished after flashing |

A pio success is an uploader result. Only the probe's `Typed health ACK` line is an ACK, and it is a health ACK, not a motor run.
