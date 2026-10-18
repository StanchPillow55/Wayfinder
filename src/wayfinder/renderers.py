"""Format committed WIR state for visual, display, speech, and tactile channels.

Renderers do not change page state and do not choose browser actions.
"""

from __future__ import annotations

from wayfinder.contracts import ACTUATOR, MOTOR_RUNS, POSES, angle_to_pulse
from wayfinder.models import Control


def tactile_payload(state_token: str, label: str, *, detach: bool = False) -> dict[str, object]:
    if state_token not in POSES or state_token not in MOTOR_RUNS:
        raise KeyError(state_token)
    angle = POSES[state_token]
    run = MOTOR_RUNS[state_token]
    return {
        "state_token": state_token,
        "actuator": ACTUATOR,
        "duty_percent": run["duty_percent"],
        "duration_ms": run["duration_ms"],
        "direction": run["direction"],
        "angle_deg": angle,
        "pulse_us": angle_to_pulse(angle),
        "label": label[:80],
        "detach": detach,
    }


def display_payload(
    *,
    title: str,
    body: str,
    branches: list[Control],
    confirmation: bool,
) -> dict[str, object]:
    return {
        "title": title[:80],
        "body": body[:180],
        "branches": [{"n": index, "label": branch.label[:60]} for index, branch in enumerate(branches[:5], start=1)],
        "confirmation": confirmation,
    }


def glance_payload(branches: list[Control], title: str, speech: str, confirmation: bool) -> dict[str, object]:
    """A camera-free, text-only glance: a few short lines for a head-up or watch-sized surface.

    This is a software projection only. No glasses SDK is attached.
    """
    lines = [title[:36]]
    if confirmation:
        lines.append("Confirm? yes / no")
    else:
        lines.extend(f"{index}. {branch.label[:34]}" for index, branch in enumerate(branches[:3], start=1))
    return {
        "lines": lines[:4],
        "quiet": True,
        "surface": "simulated_text_glance",
        "note": "Software glance view. No glasses hardware or SDK is attached.",
    }


def speech_payload(text: str) -> dict[str, object]:
    compact = " ".join(text.split())
    return {"text": compact[:280]}


def orientation_speech(summary: str, branches: list[Control], density: str) -> str:
    options = " ".join(f"{index}. {branch.label}." for index, branch in enumerate(branches, start=1))
    if density == "full":
        reasons = " ".join(
            f"{index}. {branch.rationale}" for index, branch in enumerate(branches, start=1)
        )
        return f"{summary} Options: {options} Why: {reasons}"
    if density == "brief":
        return f"{summary} Options: {options}"
    return f"{summary} Options: {options}"


def _dots(*dots: int) -> int:
    value = 0
    for dot in dots:
        value |= 1 << (dot - 1)
    return value


# Uncontracted English letters. Dot numbers follow the standard 6-dot cell.
_LETTERS = {
    "a": _dots(1),
    "b": _dots(1, 2),
    "c": _dots(1, 4),
    "d": _dots(1, 4, 5),
    "e": _dots(1, 5),
    "f": _dots(1, 2, 4),
    "g": _dots(1, 2, 4, 5),
    "h": _dots(1, 2, 5),
    "i": _dots(2, 4),
    "j": _dots(2, 4, 5),
    "k": _dots(1, 3),
    "l": _dots(1, 2, 3),
    "m": _dots(1, 3, 4),
    "n": _dots(1, 3, 4, 5),
    "o": _dots(1, 3, 5),
    "p": _dots(1, 2, 3, 4),
    "q": _dots(1, 2, 3, 4, 5),
    "r": _dots(1, 2, 3, 5),
    "s": _dots(2, 3, 4),
    "t": _dots(2, 3, 4, 5),
    "u": _dots(1, 3, 6),
    "v": _dots(1, 2, 3, 6),
    "w": _dots(2, 4, 5, 6),
    "x": _dots(1, 3, 4, 6),
    "y": _dots(1, 3, 4, 5, 6),
    "z": _dots(1, 3, 5, 6),
}

_PUNCT = {
    ".": _dots(2, 5, 6),
    ",": _dots(2),
    "?": _dots(2, 3, 6),
    "!": _dots(2, 3, 5),
    "'": _dots(3),
    "-": _dots(3, 6),
    ":": _dots(2, 5),
    ";": _dots(2, 3),
}

_CAPITAL = _dots(6)
_NUMBER = _dots(3, 4, 5, 6)


def grade1_braille(text: str) -> list[int]:
    """Uncontracted 6-dot cells for the live speech. A cell value is a dot bitmask."""
    cells: list[int] = []
    number_run = False
    for char in text:
        if char == " ":
            number_run = False
            cells.append(0)
            continue
        if char.isdigit():
            if not number_run:
                cells.append(_NUMBER)
                number_run = True
            letter = "j" if char == "0" else chr(ord("a") + int(char) - 1)
            cells.append(_LETTERS[letter])
            continue
        number_run = False
        lower = char.lower()
        if lower in _LETTERS:
            if char.isupper():
                cells.append(_CAPITAL)
            cells.append(_LETTERS[lower])
            continue
        if char in _PUNCT:
            cells.append(_PUNCT[char])
    return cells
