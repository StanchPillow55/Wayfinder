"""Format committed WIR state for visual, display, speech, and tactile channels.

Renderers do not change page state and do not choose browser actions.
"""

from __future__ import annotations

import re

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


def _first_sentence(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    cut = compact.find(". ")
    first = compact if cut < 0 else compact[: cut + 1]
    return first[:limit]


_STATUS_LEAK = re.compile(
    r"verification\s*:\s*(?:SUCCESS|PARTIAL|FAILURE|AMBIGUOUS)\b\.?|"
    r"\b(?:SUCCESS|PARTIAL|FAILURE|AMBIGUOUS)\b",
    re.IGNORECASE,
)
_ROBOTIC_DETAILS = {
    "the page matches the expected postcondition.",
    "only part of the expected postcondition is true.",
    "the page does not match the expected postcondition.",
    "nothing was checked.",
}


def _strip_status_words(text: str) -> str:
    """Verification status stays on the internal record. It is not spoken."""
    return " ".join(_STATUS_LEAK.sub(" ", text or "").split()).strip()


def _menu_name(label: str) -> str:
    return " ".join(label.split()).strip().rstrip(".")


def choice_menu(branches: list[Control], density: str) -> str:
    """Phone-menu choices. The words are the real option labels."""
    limit = 3 if density == "brief" else len(branches)
    names = []
    for branch in branches[:limit]:
        label = branch.label[:48] if density == "brief" else branch.label
        name = _menu_name(label)
        if branch.risk_class == "consequential" and density != "brief":
            name = f"{name}, which asks first"
        if name:
            names.append(name)
    if not names:
        return "You can ask what is here."
    if len(names) == 1:
        return f"You can {names[0]}."
    return "You can " + ", ".join(names[:-1]) + f", or {names[-1]}."


def still_on_page(summary: str, status: str = "") -> str:
    """What to say when a check did not pass: the page as it still is."""
    status_text = _strip_status_words(status).strip().rstrip(".")
    if status_text:
        return f"The page still says {status_text}."
    summary_text = _strip_status_words(summary)
    if not summary_text:
        return "The page is unchanged."
    return _first_sentence(summary_text)


def salient_outcome(control: Control, verification: object, summary: str) -> str:
    """One plain sentence for a check that passed. No status word."""
    observed = dict(getattr(verification, "observed", {}) or {})
    if observed.get("alert") == "requested":
        return "The arrival alert is requested."
    if observed.get("step_free_only") is True:
        return "Only step-free stations are shown."
    if observed.get("step_free_only") is False and getattr(control, "id", "") == "clear_filter":
        return "The step-free filter is cleared."
    if observed.get("view") == "evening":
        return "Evening departures are shown."
    if observed.get("view") == "overview":
        return "The daytime overview is shown."
    detail = _strip_status_words(str(getattr(verification, "detail", "") or ""))
    if detail and detail.lower() not in _ROBOTIC_DETAILS:
        return detail if detail.endswith((".", "?", "!")) else detail + "."
    summary_text = _strip_status_words(summary)
    if summary_text:
        parts = [part.strip() for part in summary_text.split(".") if part.strip()]
        if parts:
            return parts[-1] + "."
    label = _strip_status_words(control.label).rstrip(".")
    return f"{label}." if label else "Done."


def orientation_speech(summary: str, branches: list[Control], density: str) -> str:
    summary_text = _strip_status_words(summary)
    menu = choice_menu(branches[:3] if density == "brief" else branches, density)
    if density == "full":
        reasons = " ".join(f"{index}. {branch.rationale}" for index, branch in enumerate(branches, start=1))
        return f"{summary_text} {menu} Why: {reasons}".strip()
    if density == "brief":
        return f"{_first_sentence(summary_text)} {menu}".strip()
    return f"{summary_text} {menu}".strip()


def narrate_result(lead: str, branches: list[Control], density: str) -> str:
    """What the person hears after an action: what happened, then the next choices.

    Density changes length, never meaning. A verification status is never included.
    """
    spoken = _strip_status_words(lead)
    if spoken and not spoken.endswith((".", "?", "!")):
        spoken += "."
    menu = choice_menu(branches[:3] if density == "brief" else branches, density)
    if density == "brief":
        spoken = _first_sentence(spoken, 140) if spoken else ""
    if density == "full":
        reasons = " ".join(f"{index}. {branch.rationale}" for index, branch in enumerate(branches, start=1))
        return f"{spoken} {menu} Why: {reasons}".strip()
    return f"{spoken} {menu}".strip()


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
