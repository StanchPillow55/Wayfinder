"""Shared bounds for the Mac and the DevKitC-1 firmware.

The C++ firmware duplicates these numbers. tests/test_topology.py reads
firmware/devkit_tactile_adapter/src/main.cpp and checks they match.
GPIO 18 is an unverified pin assumption. See docs/adr/0001-mac-orchestrated-usb-topology.md.
"""

PROPOSAL_VERSION = "2.4"
ACTUATOR = "130_mini_dc_brushless"
ADR_ID = "ADR-001"
WIR_VERSION = "0.2"
PROTOCOL_VERSION = "0.1"

MAC_ID = "mac_orchestrator"
DEVKIT_ID = "devkit_tactile"
BOX3_ID = "esp32_s3_box3"
P4_ID = "p4_eye"

# Proposal 2.0 ids that this implementation refuses to route.
RETIRED_ENDPOINT_IDS = frozenset({"esp32_c3_display", "esp32_s3_gateway"})
BOARD_IDS = frozenset({DEVKIT_ID, BOX3_ID, P4_ID})
ALLOWED_TRANSPORTS = frozenset({"usb_serial", "usb_debug", "simulator"})

SERVO_GPIO = 18
SERVO_PWM_HZ = 50
MIN_PULSE_US = 1000
MAX_PULSE_US = 2000
MAX_ANGLE_DEG = 160
MIN_MOVE_INTERVAL_MS = 50
MAX_MESSAGE_BYTES = 1024

# Semantic poses. ADR-002 froze these tokens.
# The actuator on hand is a 130 mini DC brushless motor. It spins; it does not
# hold an angle. MOTOR_RUNS is the physical meaning. POSES remains only as a
# pulse-width hint for unflashed firmware and the on-screen pose marker.
POSES = {
    "cleared": 0,
    "orient": 30,
    "branch_ready": 55,
    "needs_confirmation": 90,
    "action_verified": 125,
    "failure": 155,
}
MOTOR_RUNS = {
    "cleared": {"duty_percent": 0, "duration_ms": 0, "direction": "stop"},
    "orient": {"duty_percent": 30, "duration_ms": 180, "direction": "forward"},
    "branch_ready": {"duty_percent": 45, "duration_ms": 240, "direction": "forward"},
    "needs_confirmation": {"duty_percent": 70, "duration_ms": 140, "direction": "forward"},
    "action_verified": {"duty_percent": 55, "duration_ms": 360, "direction": "forward"},
    "failure": {"duty_percent": 80, "duration_ms": 120, "direction": "reverse"},
}

RAW_MEDIA_KEYS = frozenset({
    "image",
    "jpeg",
    "jpg",
    "png",
    "frame",
    "frames",
    "pixels",
    "base64",
    "pcm",
    "audio",
    "wav",
    "opus",
    "video",
})


def angle_to_pulse(angle_deg: int) -> int:
    """Map 0..MAX_ANGLE_DEG onto MIN_PULSE_US..MAX_PULSE_US."""
    bounded = max(0, min(MAX_ANGLE_DEG, int(angle_deg)))
    span = MAX_PULSE_US - MIN_PULSE_US
    return MIN_PULSE_US + round(bounded * span / MAX_ANGLE_DEG)


def clamp_angle(angle_deg: int) -> int:
    return max(0, min(MAX_ANGLE_DEG, int(angle_deg)))
