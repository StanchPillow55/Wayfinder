#!/usr/bin/env bash
# Wayfinder Mac bring-up: discover the port, flash health-only DevKit firmware, probe a typed ACK.
#
# This script has never flashed a board in this repo's test runs. It does not know which
# port is which board: you pass the port. It flashes ONLY devkit_health_only, unless you
# pass --motor-bench together with --devkit-port (and the gates below pass).
#
# See scripts/hardware/README.md. Written for macOS bash 3.2; no bashisms newer than that.

set -u
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_DIR="${WAYFINDER_BRINGUP_DIR:-$REPO/.wayfinder-local}"   # untracked, see .gitignore
SNAPSHOT="$LOCAL_DIR/ports.snapshot"
ENV_FILE="$LOCAL_DIR/bench.env"
DEVKIT_DIR="$REPO/firmware/devkit_tactile_adapter"
SELF="scripts/hardware/bringup.sh"

MODE="list"
DEVKIT_PORT=""
PROBE_BOARD=""
PROBE_PORT=""
MOTOR_BENCH=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: $SELF [mode]

  (no flags)                    list serial ports, env state, and which tools are installed
  --snapshot                    save the current port list (run with the boards UNPLUGGED)
  --diff                        compare ports now against the saved snapshot
  --devkit-port PORT            flash devkit_health_only to PORT, then send a health probe
  --devkit-port PORT --motor-bench
                                flash devkit_motor_bench instead (opt-in, gated, see README)
  --probe BOARD --port PORT     health probe only, no flash. BOARD: devkit_tactile,
                                esp32_s3_box3, p4_eye
  --dry-run                     print what would run; no flash, no probe, no port check
  -h, --help                    this text

The script never picks a port for you and never assigns a port to a board.
Nothing here is evidence of a flash, a motor run, or a physical ACK until it prints one.
EOF
}

die() { echo "$SELF: $1" >&2; exit "${2:-2}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) MODE="snapshot" ;;
    --diff) MODE="diff" ;;
    --devkit-port) [ $# -ge 2 ] || die "--devkit-port needs a path"; DEVKIT_PORT="$2"; shift ;;
    --devkit-port=*) DEVKIT_PORT="${1#*=}" ;;
    --probe) [ $# -ge 2 ] || die "--probe needs a board id"; PROBE_BOARD="$2"; shift ;;
    --probe=*) PROBE_BOARD="${1#*=}" ;;
    --port) [ $# -ge 2 ] || die "--port needs a path"; PROBE_PORT="$2"; shift ;;
    --port=*) PROBE_PORT="${1#*=}" ;;
    --motor-bench) MOTOR_BENCH=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

# ---- tools ------------------------------------------------------------------

pick_python() {
  if [ -n "${WAYFINDER_PYTHON:-}" ]; then echo "$WAYFINDER_PYTHON"
  elif [ -x "$REPO/.venv/bin/python" ]; then echo "$REPO/.venv/bin/python"
  else command -v python3 || true
  fi
}
PY="$(pick_python)"

find_pio() {
  if command -v pio >/dev/null 2>&1; then command -v pio
  elif command -v platformio >/dev/null 2>&1; then command -v platformio
  elif [ -x "$REPO/.venv/bin/pio" ]; then echo "$REPO/.venv/bin/pio"
  fi
}
PIO="$(find_pio)"

have_idf() { command -v idf.py >/dev/null 2>&1; }

py_run() { PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}" "$PY" "$@"; }
have_wayfinder() { [ -n "$PY" ] && py_run -c "import wayfinder.devices" >/dev/null 2>&1; }
have_pyserial() { [ -n "$PY" ] && "$PY" -c "import serial" >/dev/null 2>&1; }

pio_install_cmd() {
  if [ -x "$REPO/.venv/bin/python" ]; then
    echo "\"$REPO/.venv/bin/python\" -m pip install platformio"
  else
    echo "python3 -m pip install --user platformio"
  fi
}

pyserial_install_cmd() {
  if [ -x "$REPO/.venv/bin/python" ]; then
    echo "cd \"$REPO\" && \"$REPO/.venv/bin/python\" -m pip install -e '.[serial]'"
  else
    echo "cd \"$REPO\" && python3 -m venv .venv && .venv/bin/python -m pip install -e '.[serial]'"
  fi
}

idf_install_cmd() {
  echo "brew install cmake ninja dfu-util && mkdir -p ~/esp && cd ~/esp && git clone -b v5.4 --recursive https://github.com/espressif/esp-idf.git && cd esp-idf && ./install.sh esp32p4 && . ./export.sh"
}

# ---- ports ------------------------------------------------------------------

# Every callout device the OS exposes, minus macOS built-ins that are never a board.
current_ports() {
  local p
  for p in /dev/cu.* /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$p" ] || continue
    case "$p" in
      *Bluetooth-Incoming-Port*|*debug-console*) continue ;;
    esac
    echo "$p"
  done | sort -u
}

is_refused_port() {
  case "$1" in
    *Bluetooth-Incoming-Port*|*debug-console*) return 0 ;;
  esac
  return 1
}

print_tools() {
  echo "Tools:"
  if [ -n "$PIO" ]; then echo "  PlatformIO:  found ($PIO)"; else echo "  PlatformIO:  MISSING"; fi
  if have_idf; then echo "  idf.py:      found"; else echo "  idf.py:      MISSING (only needed for P4X-EYE)"; fi
  if [ -z "$PY" ]; then echo "  python:      MISSING"
  else
    echo "  python:      $PY"
    if have_wayfinder; then echo "  wayfinder:   importable"; else echo "  wayfinder:   NOT importable"; fi
    if have_pyserial; then echo "  pyserial:    importable"; else echo "  pyserial:    MISSING (needed for the probe)"; fi
  fi
}

cmd_list() {
  echo "Serial ports now (macOS built-ins that are never a board are hidden):"
  local ports; ports="$(current_ports)"
  if [ -z "$ports" ]; then echo "  (none). Check the cable carries data, then try another socket."; else
    echo "$ports" | sed 's/^/  /'
  fi
  echo
  if have_wayfinder; then
    py_run -m wayfinder.devices 2>/dev/null | sed 's/^/  /' || true
  fi
  echo
  print_tools
  echo
  if [ -f "$ENV_FILE" ]; then echo "Local env snippet: $ENV_FILE (source it; it is gitignored)"; else echo "Local env snippet: none yet (written after a typed health ACK)"; fi
  echo
  echo "This script does not know which port is which board. Next:"
  echo "  1. Unplug every board, then:   $SELF --snapshot"
  echo "  2. Plug in ONE data cable, wait 3 s, then:   $SELF --diff"
  echo "  3. Pass the new port yourself:   $SELF --devkit-port <path>"
}

cmd_snapshot() {
  mkdir -p "$LOCAL_DIR" || die "cannot create $LOCAL_DIR"
  current_ports > "$SNAPSHOT"
  local n; n="$(wc -l < "$SNAPSHOT" | tr -d ' ')"
  echo "Saved $n port(s) to $SNAPSHOT. This is a local, gitignored file."
  if [ "$n" -gt 0 ]; then
    echo "These are the ports present RIGHT NOW. If a board is plugged in, unplug it and re-run --snapshot."
    sed 's/^/  /' "$SNAPSHOT"
  fi
  echo "Next: plug in ONE board by a data cable, wait 3 s, then: $SELF --diff"
}

cmd_diff() {
  [ -f "$SNAPSHOT" ] || die "no snapshot at $SNAPSHOT. Unplug all boards and run: $SELF --snapshot"
  local now added removed
  now="$(mktemp -t wayfinder-ports.XXXXXX)" || die "mktemp failed"
  current_ports > "$now"
  added="$(comm -13 "$SNAPSHOT" "$now")"
  removed="$(comm -23 "$SNAPSHOT" "$now")"
  rm -f "$now"
  if [ -n "$removed" ]; then
    echo "Ports gone since the snapshot:"; echo "$removed" | sed 's/^/  /'
  fi
  if [ -z "$added" ]; then
    echo "No new port since the snapshot."
    echo "Many USB-C cables are charge-only. Try another cable, another socket (DevKit v1.1: the one marked UART),"
    echo "or hold BOOT while plugging in. Stop after three tries and log it in docs/hardware/sprint.md."
    return 1
  fi
  echo "New port(s) since the snapshot:"
  echo "$added" | sed 's/^/  /'
  local count; count="$(echo "$added" | wc -l | tr -d ' ')"
  if [ "$count" -gt 1 ]; then
    echo "More than one new port. Unplug everything, re-run --snapshot, and plug in one board at a time."
    return 1
  fi
  echo
  echo "I will not decide what board this is. If you plugged in the DevKitC-1, the next command is:"
  echo "  $SELF --devkit-port $added"
  echo "(BOX-3 and P4X-EYE: see 'Next' in $REPO/scripts/hardware/README.md.)"
}

# ---- gates and flashing -----------------------------------------------------

check_port_arg() {   # $1 = port, $2 = flag name
  local port="$1" flag="$2"
  [ -n "$port" ] || die "$flag needs a port path. This script will not guess one."
  case "$port" in
    /dev/*) ;;
    *) die "refusing '$port': expected a /dev/cu.* style path." ;;
  esac
  is_refused_port "$port" && die "refusing '$port': that is a macOS built-in, not a board."
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry run] not checking that $port exists."
    return 0
  fi
  [ -e "$port" ] || die "$port does not exist. Run: $SELF --diff"
  if ! current_ports | grep -Fxq "$port"; then
    die "$port is not a recognised serial device path. Run: $SELF --diff"
  fi
}

write_env_line() {   # $1 = VAR, $2 = value. Keeps other lines. Local, gitignored file.
  local var="$1" value="$2" tmp
  mkdir -p "$LOCAL_DIR" || die "cannot create $LOCAL_DIR"
  tmp="$(mktemp -t wayfinder-env.XXXXXX)" || die "mktemp failed"
  if [ -f "$ENV_FILE" ]; then grep -v "^export $var=" "$ENV_FILE" > "$tmp" || true; fi
  printf 'export %s=%q\n' "$var" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

env_file_value() {   # $1 = VAR; prints its value from our own local ENV_FILE, or nothing
  [ -f "$ENV_FILE" ] || return 0
  ( . "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${!1:-}" )
}

preflight_tools_or_stop() {   # stop BEFORE any flash if a tool is missing
  local missing=0
  if [ -z "$PIO" ]; then
    echo "PlatformIO is not installed. Not flashing. Install it, then re-run this same command:"
    echo "  $(pio_install_cmd)"
    missing=1
  fi
  if ! have_wayfinder || ! have_pyserial; then
    echo "pyserial/wayfinder are not importable, so the health probe cannot run. Not flashing. Install:"
    echo "  $(pyserial_install_cmd)"
    missing=1
  fi
  [ "$missing" -eq 0 ] || exit 3
}

run_probe() {   # $1 = board id, $2 = port. Health only; cannot command the motor.
  local board="$1" port="$2"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry run] would run: python -m wayfinder.devices --probe $board --port $port"
    return 0
  fi
  py_run -m wayfinder.devices --probe "$board" --port "$port"
}

record_ack() {   # $1 = board env var, $2 = port. Only called after a typed ACK.
  [ "$DRY_RUN" -eq 1 ] && { echo "[dry run] would write $1 to $ENV_FILE after an ACK."; return 0; }
  write_env_line "$1" "$2"
  echo "Typed health ACK recorded. Wrote $1 to $ENV_FILE (local, gitignored)."
  echo "  source \"$ENV_FILE\""
}

print_next_boards() {
  cat <<EOF

Next boards (this script does not flash them; the commands are for you to run, one board at a time):

BOX-3 (main unit USB-C, not the dock's power-only one). Unverified scaffold, proves the envelope only:
  $SELF --snapshot            # with every board unplugged
  $SELF --diff                # after plugging in the BOX-3 only
  cd "$REPO/firmware/esp32_s3_box3" && "${PIO:-pio}" run -t upload --upload-port <port from --diff>
  $SELF --probe esp32_s3_box3 --port <same port>
  (PlatformIO board id 'esp32s3box' is a guess. Check: pio boards box)

P4X-EYE (port marked Debug). Needs ESP-IDF, unverified scaffold:
EOF
  if have_idf; then
    echo "  idf.py found."
  else
    echo "  idf.py is MISSING. Install ESP-IDF first (release must list esp32p4; v5.4 is an assumption):"
    echo "    $(idf_install_cmd)"
  fi
  cat <<EOF
  cd "$REPO/firmware/p4_eye" && idf.py set-target esp32p4 && idf.py -p <port from --diff> flash
  $SELF --probe p4_eye --port <same port>
EOF
}

cmd_flash_devkit() {
  check_port_arg "$DEVKIT_PORT" "--devkit-port"
  local envname="devkit_health_only"

  if [ "$MOTOR_BENCH" -eq 1 ]; then
    envname="devkit_motor_bench"
    local recorded; recorded="$(env_file_value WAYFINDER_DEVKIT_PORT)"
    if [ "$recorded" != "$DEVKIT_PORT" ]; then
      die "refusing devkit_motor_bench: no typed health ACK is recorded for $DEVKIT_PORT in this session.
Flash and probe the health-only build on this exact port first:
  $SELF --devkit-port $DEVKIT_PORT"
    fi
    echo "MOTOR BENCH requested for $DEVKIT_PORT. Only continue if ALL of these are done and logged:"
    echo "  - driver/ESC identified, supply measured with a meter, ratings checked (docs/hardware/sprint.md)"
    echo "  - motor supply is EXTERNAL with a common ground, never the DevKit 5V or 3V3 pin"
    echo "  - motor supply is switched OFF, shaft is clear, a switch is in reach"
    echo "GPIO 18 is UNVERIFIED. This build drives it."
    if [ "$DRY_RUN" -eq 0 ]; then
      [ -t 0 ] || die "motor bench needs an interactive terminal to confirm."
      printf "Type 'supply off' to flash the motor-bench build, anything else aborts: "
      local answer; read -r answer
      [ "$answer" = "supply off" ] || die "aborted. Nothing was flashed."
    else
      echo "[dry run] would ask you to type 'supply off'."
    fi
  fi

  preflight_tools_or_stop

  local cmd="\"${PIO:-pio}\" run -e $envname -t upload --upload-port \"$DEVKIT_PORT\""
  echo "Flashing $envname to $DEVKIT_PORT:"
  echo "  (cd \"$DEVKIT_DIR\" && $cmd)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry run] not flashing."
  else
    if ! ( cd "$DEVKIT_DIR" && "$PIO" run -e "$envname" -t upload --upload-port "$DEVKIT_PORT" ); then
      echo "Flash failed. Nothing was acked. Hold BOOT, tap RESET, release BOOT, and retry the same command." >&2
      echo "Do not conclude the board or port is fine." >&2
      exit 4
    fi
    echo "pio reported success. That is a compiler/uploader result, not a health ACK."
    sleep 2
    if [ ! -e "$DEVKIT_PORT" ]; then
      echo "$DEVKIT_PORT disappeared after flashing. Native USB boards can re-enumerate under a new name."
      echo "Run: $SELF --diff   then   $SELF --probe devkit_tactile --port <new port>"
      exit 5
    fi
  fi

  echo "Health probe (sends 'health' only, never tactile.render):"
  if run_probe devkit_tactile "$DEVKIT_PORT"; then
    record_ack WAYFINDER_DEVKIT_PORT "$DEVKIT_PORT"
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry run] nothing was flashed, probed, or acked. Showing the follow-up text only."
    elif [ "$MOTOR_BENCH" -eq 1 ]; then
      echo "Motor-bench build answered health. The motor has NOT been commanded and no power was applied by this script."
      echo "Switch the external supply on only per docs/hardware/sprint.md, then send 'orient' from the app."
    else
      echo "Health-only build: the motor signal pin is never configured. The motor bench is a separate, opt-in step:"
      echo "  $SELF --devkit-port $DEVKIT_PORT --motor-bench"
    fi
    print_next_boards
    return 0
  fi
  echo "No typed ACK. Do not export the port for this board. Opening the port can reset an ESP32; retry once:" >&2
  echo "  $SELF --probe devkit_tactile --port $DEVKIT_PORT" >&2
  exit 5
}

cmd_probe_only() {
  check_port_arg "$PROBE_PORT" "--port"
  case "$PROBE_BOARD" in
    devkit_tactile|esp32_s3_box3|p4_eye) ;;
    *) die "unknown board '$PROBE_BOARD'. Use devkit_tactile, esp32_s3_box3, or p4_eye." ;;
  esac
  if [ "$DRY_RUN" -eq 0 ]; then
    have_wayfinder && have_pyserial || { echo "Install first:"; echo "  $(pyserial_install_cmd)"; exit 3; }
  fi
  local var
  case "$PROBE_BOARD" in
    devkit_tactile) var=WAYFINDER_DEVKIT_PORT ;;
    esp32_s3_box3) var=WAYFINDER_BOX3_PORT ;;
    p4_eye) var=WAYFINDER_P4_PORT ;;
  esac
  if run_probe "$PROBE_BOARD" "$PROBE_PORT"; then
    record_ack "$var" "$PROBE_PORT"
    return 0
  fi
  exit 5
}

# ---- dispatch ---------------------------------------------------------------

if [ "$MOTOR_BENCH" -eq 1 ] && [ -z "$DEVKIT_PORT" ]; then
  die "--motor-bench needs --devkit-port <path> as well. Nothing was flashed."
fi
if [ -n "$PROBE_BOARD" ] && [ -n "$DEVKIT_PORT" ]; then
  die "use either --devkit-port (flash + probe) or --probe BOARD --port PORT (probe only), not both."
fi
if [ -n "$PROBE_PORT" ] && [ -z "$PROBE_BOARD" ]; then
  die "--port is only for --probe. To flash the DevKit use --devkit-port."
fi

if [ -n "$DEVKIT_PORT" ]; then
  cmd_flash_devkit
elif [ -n "$PROBE_BOARD" ]; then
  cmd_probe_only
else
  case "$MODE" in
    snapshot) cmd_snapshot ;;
    diff) cmd_diff ;;
    *) cmd_list ;;
  esac
fi
