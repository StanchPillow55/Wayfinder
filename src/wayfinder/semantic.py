"""One semantic state per turn, and the channel views that read it.

build_state() is the only place a turn's meaning is assembled: situation, what matters,
available actions with consequence and confidence, warnings, the prior path, and the user's
goal. Speech, visual cards, Braille, the tactile cue, and the quiet glance are all rendered
from it by compile_channels(). A renderer may shorten or reformat; it may not re-interpret.
"""

from __future__ import annotations

SCHEMA = "wayfinder.semantic_state/1"

RENDERERS = {
    "all": {
        "label": "Everything on",
        "audio": True,
        "foreground": ["visual", "audio", "braille", "tactile", "glance"],
        "arm": [],
    },
    "visual": {"label": "Visual", "audio": False, "foreground": ["visual", "glance"], "arm": []},
    "audio": {"label": "Audio", "audio": True, "foreground": ["audio"], "arm": []},
    "tactile": {"label": "Tactile and Braille", "audio": False, "foreground": ["braille", "tactile"], "arm": []},
    "hands_free": {"label": "Hands-free", "audio": True, "foreground": ["audio"], "arm": ["voice", "camera"]},
}


def build_state(
    *,
    turn_id: str,
    page: dict,
    mode: str,
    branches: list[dict],
    pending: dict | None,
    verification: dict | None,
    goal: dict,
    goal_plan: dict | None,
    journey: list[dict],
    uncertainty: list[str],
    repairs: list[dict],
    density: str,
    selected: dict | None,
) -> dict:
    matters: list[str] = []
    if goal.get("text"):
        matters.append(f"Goal: {goal['text']}")
    if goal.get("constraints"):
        matters.append("Constraints: " + ", ".join(goal["constraints"]))
    if page.get("answer"):
        matters.append(f"Open answer: {page['answer'][:160]}")
    if page.get("status"):
        matters.append(f"Page says: {page['status'][:160]}")
    if verification:
        matters.append(f"Last check: {verification['status']}")
    if pending:
        matters.append(f"Waiting for your yes or no: {pending['label']}")
    warnings: list[str] = list(uncertainty)
    if page.get("untrusted_text"):
        warnings.append("Untrusted page text was shown but not followed.")
    if repairs:
        warnings.append(f"{len(repairs)} control names were inferred from nearby text. The live page was not changed.")
    if page.get("kind") == "fixture":
        warnings.append("Harbor Line is the labeled offline fixture, not the live browser.")
    if page.get("supported") is False:
        warnings.append("This page is not a supported pattern. No actions were invented.")
    actions = []
    for index, branch in enumerate(branches, start=1):
        inferred = "(inferred name)" in branch["label"]
        actions.append(
            {
                "n": index,
                "id": branch["id"],
                "label": branch["label"],
                "consequence": branch["preview"],
                "risk_class": branch["risk_class"],
                "risk_label": branch["risk_label"],
                "needs_confirmation": branch["risk_class"] == "consequential",
                "confidence": "inferred" if inferred else "grounded",
                "capability": branch["capability"],
            }
        )
    return {
        "schema": SCHEMA,
        "turn_id": turn_id,
        "situation": {
            "title": page.get("title", ""),
            "url": page.get("url", ""),
            "purpose": page.get("purpose", ""),
            "summary": page.get("summary", ""),
            "family": page.get("family", ""),
            "mode": mode,
        },
        "what_matters": matters[:8],
        "actions": actions,
        "warnings": warnings[:8],
        "prior_path": [item["text"] for item in journey[-5:]],
        "user_goal": {
            "text": goal.get("text", ""),
            "constraints": list(goal.get("constraints") or []),
            "plan_status": (goal_plan or {}).get("status", "none"),
            "steps_done": len((goal_plan or {}).get("steps") or []),
        },
        "confirmation": pending,
        "density": density,
        "selected_capability": selected,
    }


def glance_from_state(state: dict) -> dict:
    """Quiet, text-only glance. Reads the state; invents nothing."""
    title = (state["situation"]["title"] or "Wayfinder")[:36]
    lines = [title]
    if state["confirmation"]:
        lines.append("Confirm? yes / no")
    else:
        lines.extend(f"{a['n']}. {a['label'][:34]}" for a in state["actions"][:3])
    return {
        "lines": lines[:4],
        "quiet": True,
        "surface": "simulated_text_glance",
        "from": SCHEMA,
        "note": "Software glance view. No glasses hardware or SDK is attached.",
    }


def visual_from_state(state: dict) -> dict:
    return {
        "headline": state["situation"]["title"],
        "from": SCHEMA,
        "cards": [
            {
                "n": a["n"],
                "label": a["label"],
                "consequence": a["consequence"],
                "risk_label": a["risk_label"],
                "confidence": a["confidence"],
                "needs_confirmation": a["needs_confirmation"],
            }
            for a in state["actions"]
        ],
    }


def channels_view(*, renderer: str, muted: bool, status: dict[str, str], plane_channels: dict[str, list[str]], p4_connected: bool) -> dict:
    """Input and output channels. These are channels, not settings tied to a person."""
    fg = set(RENDERERS[renderer]["foreground"])
    mic = status.get("mic", "available")
    camera = status.get("camera", "available")
    inputs = [
        {"id": "keyboard", "label": "Keyboard and typed commands", "status": "armed", "note": "Always on."},
        {"id": "voice", "label": "Voice (microphone)", "status": mic,
         "note": "Browser speech recognition. Starts on your first press, then stays open until you mute it."},
        {"id": "camera", "label": "Camera (laptop webcam)", "status": camera,
         "note": "Laptop-camera motion demo. Not gaze tracking. Video stays in this tab."},
        {"id": "switches", "label": "Switches (on-screen stand-ins)", "status": "armed",
         "note": "Next, confirm, back, stop. Same actions as the DevKit buttons."},
        {"id": "touch", "label": "BOX-3 touch and speech", "status": "available" if "touch" in plane_channels.get("input", []) else "not_connected",
         "note": "Simulated until a board answers over USB."},
        {"id": "p4_eye", "label": "P4X-EYE camera events", "status": "available" if p4_connected else "not_connected",
         "note": "Not live. The laptop webcam is the vision path today."},
    ]
    outputs = [
        {"id": "visual", "label": "Visual cards", "status": "on", "foreground": "visual" in fg},
        {"id": "audio", "label": "Spoken audio", "status": "muted" if muted else "on", "foreground": "audio" in fg},
        {"id": "braille", "label": "Braille plate (software)", "status": "on", "foreground": "braille" in fg},
        {"id": "tactile", "label": "Tactile motor cue (software)", "status": "simulated", "foreground": "tactile" in fg},
        {"id": "glance", "label": "Quiet glance text", "status": "on", "foreground": "glance" in fg},
    ]
    return {
        "input": inputs,
        "output": outputs,
        "renderer": renderer,
        "renderer_label": RENDERERS[renderer]["label"],
        "renderers": [{"id": key, "label": value["label"]} for key, value in RENDERERS.items()],
        "note": "Every permitted channel is available at once. Use whichever suits you. There is nothing to set up.",
    }


def compile_channels(*, state: dict, speech: str, braille: dict, tactile: dict, muted: bool) -> dict:
    return {
        "source": {"schema": SCHEMA, "turn_id": state["turn_id"]},
        "speech": {"text": speech, "muted": muted, "from": SCHEMA},
        "visual": visual_from_state(state),
        "braille": {**braille, "from": SCHEMA},
        "tactile": {**tactile, "from": SCHEMA},
        "glance": glance_from_state(state),
    }


def hardware_view(*, physical_motor_ran: bool, devices: list[dict], p4_connected: bool) -> dict:
    return {
        "scaffolding": True,
        "physical_motor_ran": physical_motor_ran,
        "motor_has_spun": False,
        "firmware_flashed": False,
        "p4_connected": p4_connected,
        "p4_live": False,
        "statement": (
            "Hardware is scaffolding. The firmware has not been flashed, the motor has not spun, and the P4X-EYE is not "
            "connected or live. The laptop webcam and the on-screen stand-ins are the working paths today."
            if not physical_motor_ran
            else "A DevKit answered over USB. That acknowledgement is not proof the motor rotated."
        ),
        "devices": devices,
    }
