"""Normalize text, touch, and speech into intents. No permissions and no tools."""

from __future__ import annotations

import re

from wayfinder.models import Control, Intent

_NUMBER_WORDS = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "five": 5,
    "fifth": 5,
}

_WHAT_IS_HERE = {
    "what is here",
    "what's here",
    "whats here",
    "where am i",
    "where am i now",
    "what can i do",
    "what can i do here",
}

_CONFIRM = {"yes", "confirm", "do it", "go ahead", "submit"}
_DENY = {"no", "nope", "deny", "don't", "do not", "do not do that"}
_STOP = {"stop", "cancel", "never mind", "nevermind"}
_INJECTION_MARKERS = ("ignore previous instructions", "ignore all previous")


def _is_injection(normalized: str) -> bool:
    """Page and pasted instructions are data. They cannot select or confirm."""
    return any(marker in normalized for marker in _INJECTION_MARKERS)


def _normalize(text: str) -> str:
    lowered = text.strip().lower()
    lowered = lowered.replace("’", "'")
    lowered = re.sub(r"[?!.,]+", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _selection(text: str) -> int | None:
    words = text.split()
    if len(words) == 1 and words[0] in _NUMBER_WORDS:
        return _NUMBER_WORDS[words[0]]
    match = re.search(
        r"\b(?:number|option|branch|#)\s*(\d+)\b|\b(?:number|option|branch)\s+(one|two|three|four|five)\b",
        text,
    )
    if match:
        digit, word = match.group(1), match.group(2)
        if digit:
            return int(digit)
        return _NUMBER_WORDS[word]
    lone = re.fullmatch(r"\d+", text)
    return int(lone.group(0)) if lone else None


def _label_match(text: str, branches: list[Control]) -> str | None | str:
    """Return an action id, 'ambiguous', or None."""
    scored: list[tuple[int, str]] = []
    for branch in branches:
        label = branch.label.lower()
        score = 0
        if text == label:
            score = 100
        elif label in text:
            score = 80
        else:
            tokens = set(label.split())
            overlap = tokens & set(text.split())
            if len(overlap) >= 2:
                score = 20 + len(overlap)
        if score:
            scored.append((score, branch.id))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 20:
        return "ambiguous"
    return scored[0][1]


_ENTRY_SEARCH = re.compile(r"^(?:search|find|look up)(?:\s+for)?\s+(?P<v>.+)$", re.IGNORECASE)
_ENTRY_SET = re.compile(
    r"^(?:set|fill in|fill|enter|type|put)\s+(?:the\s+)?(?P<f>.+?)\s+(?:to|with|as)\s+(?P<v>.+)$",
    re.IGNORECASE,
)
_ENTRY_INTO = re.compile(
    r"^(?:type|enter|put)\s+(?P<v>.+?)\s+(?:in|into)\s+(?:the\s+)?(?P<f>.+)$",
    re.IGNORECASE,
)


_ENTRY_CHOOSE = re.compile(
    r"^(?:choose|select|pick)\s+(?P<v>.+?)\s+(?:for|in|from|as)\s+(?:the\s+)?(?P<f>.+)$",
    re.IGNORECASE,
)


def _entry(raw: str) -> tuple[str, str] | None:
    """A typed entry the user asked for. The field is checked against the page later."""
    cleaned = raw.strip().rstrip(".!?").strip()
    match = _ENTRY_INTO.match(cleaned)
    if match:
        return match.group("f").strip(), match.group("v").strip().strip("\"'")
    match = _ENTRY_CHOOSE.match(cleaned)
    if match:
        return match.group("f").strip(), match.group("v").strip().strip("\"'")
    match = _ENTRY_SET.match(cleaned)
    if match:
        return match.group("f").strip(), match.group("v").strip().strip("\"'")
    match = _ENTRY_SEARCH.match(cleaned)
    if match:
        return "search", match.group("v").strip().strip("\"'")
    return None


def parse(text: str, branches: list[Control], *, channel: str = "text", pending: bool = False) -> Intent:
    raw = text.strip()
    normalized = _normalize(raw)
    if not normalized:
        return Intent(type="unknown", text=raw, channel=channel)
    if _is_injection(normalized):
        return Intent(type="unknown", text=raw, channel=channel)

    if normalized in _STOP or normalized.startswith("stop"):
        return Intent(type="stop", text=raw, channel=channel)
    if normalized in _CONFIRM:
        return Intent(type="confirm", text=raw, channel=channel)
    if normalized in _DENY:
        return Intent(type="deny", text=raw, channel=channel)
    if normalized in {"go back", "back"}:
        return Intent(type="go_back", text=raw, channel=channel)
    if normalized in {"less detail", "less", "shorter"}:
        return Intent(type="less_detail", text=raw, channel=channel)
    if normalized in {"more detail", "more", "longer"}:
        return Intent(type="more_detail", text=raw, channel=channel)
    if normalized in {"continue", "continue please"}:
        return Intent(type="continue", text=raw, channel=channel)
    if normalized in _WHAT_IS_HERE or normalized.startswith("what is here"):
        return Intent(type="what_is_here", text=raw, channel=channel)
    if normalized in {"explain", "tell me more"} or normalized.startswith("explain"):
        return Intent(type="explain", text=raw, channel=channel)
    if normalized == "compare" or normalized.startswith("compare "):
        return Intent(type="compare", text=raw, channel=channel)
    if normalized in {"that", "that one"}:
        if len(branches) == 1:
            return Intent(type="select", text=raw, selection=1, channel=channel)
        return Intent(type="ambiguous", text=raw, channel=channel)

    selection = _selection(normalized)
    if selection is not None:
        return Intent(type="select", text=raw, selection=selection, channel=channel)

    entry = _entry(raw)
    if entry is not None and entry[1]:
        return Intent(type="enter_text", text=raw, channel=channel, field=entry[0], value=entry[1][:120])

    matched = _label_match(normalized, branches)
    if matched == "ambiguous":
        return Intent(type="ambiguous", text=raw, channel=channel)
    if isinstance(matched, str):
        return Intent(type="select", text=raw, action_id=matched, channel=channel)

    if pending and normalized in {"cancel that"}:
        return Intent(type="deny", text=raw, channel=channel)
    return Intent(type="unknown", text=raw, channel=channel)


def constraints_in(text: str) -> list[str]:
    """Preferences from the user's words. Page text must not be passed here."""
    normalized = _normalize(text)
    if _is_injection(normalized):
        return []
    found: list[str] = []
    if any(token in normalized for token in ("step-free", "step free", "accessible")):
        found.append("step_free")
    if any(token in normalized for token in ("evening", "tonight")):
        found.append("evening")
    if any(token in normalized for token in ("alert", "notify")):
        found.append("alert")
    return found
