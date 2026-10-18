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
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard all previous",
    "system prompt",
)

_WAIT = {"wait", "hold on", "pause", "one moment", "wait a moment", "wait please"}
_REPEAT = {"what did you just say", "say that again", "what was that", "repeat that", "what did you say"}
_EXPLAIN_THAT = {"explain that", "why did you do that", "why that", "why did you choose that", "why"}
_READ_ALL = {"read everything", "read it all", "read all", "read everything to me"}
_JUST_DO_IT = {"just do it for me", "just do it", "do it for me", "you do it", "you do it for me", "take care of it"}
_WHERE_WAS_I = {
    "where was i", "where was i before", "what did i do", "show my path", "my path", "my history",
    "history", "show my history", "where have i been", "journey",
}
_MUTE = {"mute", "mute audio", "mute speech", "audio off", "speech off", "turn audio off", "silence audio"}
_UNMUTE = {"unmute", "unmute audio", "unmute speech", "audio on", "speech on", "turn audio on"}
_WHO = {"who is acting", "which capability", "what capability", "which capability is acting", "who is working"}
_RENDERERS = {
    "visual": "visual",
    "screen": "visual",
    "audio": "audio",
    "speech": "audio",
    "tactile": "tactile",
    "braille": "tactile",
    "hands free": "hands_free",
    "handsfree": "hands_free",
    "hands-free": "hands_free",
}
_RENDERER = re.compile(
    r"^(?:(?:switch|change)(?: the)?(?: renderer| output)?(?: to)?|use|go to)\s+(?P<r>visual|screen|audio|speech|tactile|braille|hands free|handsfree|hands-free)(?: mode| output)?$"
)
_SAVE_PROJECT = (
    re.compile(r"^(?:save|name)(?: this| my| the)?(?: project| workspace| search| task)? (?:as|called|named) (?P<n>[a-z0-9 ]{2,60})$"),
    re.compile(r"^(?:save|name)(?: this| my| the)? (?:project|workspace|search|task) (?P<n>[a-z0-9 ]{2,60})$"),
)
_CONTINUE_PROJECT = re.compile(
    r"^(?:continue|resume|reopen|pick up)(?: with)?(?: my| the)?(?: project| workspace)?\s+(?P<n>[a-z0-9 ]{2,60})$"
)
_FORGET = re.compile(r"^(?:forget|drop|remove|clear)(?: that| the| my)?(?: (?P<n>[a-z$0-9 -]{2,40}?))?(?: constraint| preference)$")
_LIMIT = re.compile(r"^(?:show|give me|use|limit(?: options)? to|only)(?: me)?(?: about| just)?\s+(?P<n>3|three|5|five)(?: options| choices| branches)?$")
_FEWER = {"fewer options", "fewer choices", "shorter list", "fewer branches"}
_MOREOPT = {"more options", "more choices", "longer list", "more branches"}
_PREFER = re.compile(r"^(?:i )?prefer (?P<c>audio|speech|braille|tactile|visual|screen|voice|keyboard|camera|switches)$")
_RESET_PREFS = {"reset my preferences", "reset preferences", "forget my preferences"}
_GOAL_PREFIX = re.compile(
    r"^(?:(?P<interrupt>actually|instead|new priority|my new priority is)[:,]?\s*)?"
    r"(?:my goal is|goal:?|i want to|i need to|i would like to|i'd like to|id like to|i want|i need|help me(?: to)?|can you|please)\s+(?P<g>.+)$"
)
_INTERRUPT_ONLY = re.compile(r"^(?P<interrupt>actually|instead|new priority|my new priority is)[:,]?\s+(?P<g>.+)$")
_GOAL_VERB = re.compile(
    r"^(?:find|request|reserve|book|apply|buy|order|purchase|add|put|take me|go to the|review|visit)\b.+$"
)
_GOAL_QUESTION = re.compile(r"^(?:show me|tell me|what are|what is the|when is|when are|where is|who is|how do i|how much)\b.+$")


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
    meta = _meta(raw, normalized, channel)
    if meta is not None:
        return meta
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

    # A stated outcome. A label that names a current option still wins. Search and typed
    # entries keep their own grammar below.
    if _GOAL_VERB.match(normalized) and not normalized.startswith(("search ", "look up")):
        exact = next((b for b in branches if b.label.lower() == normalized or b.label.lower() in normalized), None)
        if exact is not None:
            return Intent(type="select", text=raw, action_id=exact.id, channel=channel)
        return Intent(type="set_goal", text=raw, channel=channel)

    entry = _entry(raw)
    if entry is not None and entry[1]:
        return Intent(type="enter_text", text=raw, channel=channel, field=entry[0], value=entry[1][:120])

    matched = _label_match(normalized, branches)
    if matched == "ambiguous":
        return Intent(type="ambiguous", text=raw, channel=channel)
    if isinstance(matched, str):
        return Intent(type="select", text=raw, action_id=matched, channel=channel)
    if _GOAL_QUESTION.match(normalized):
        return Intent(type="set_goal", text=raw, channel=channel)

    if pending and normalized in {"cancel that"}:
        return Intent(type="deny", text=raw, channel=channel)
    return Intent(type="unknown", text=raw, channel=channel)


def _meta(raw: str, normalized: str, channel: str) -> Intent | None:
    """Interruptions, memory, goals, and output switches. None of these touch a page directly."""
    if normalized in _WAIT:
        return Intent(type="wait", text=raw, channel=channel)
    if normalized in _REPEAT:
        return Intent(type="repeat", text=raw, channel=channel)
    if normalized in _EXPLAIN_THAT:
        return Intent(type="explain_that", text=raw, channel=channel)
    if normalized in _READ_ALL:
        return Intent(type="read_all", text=raw, channel=channel)
    if normalized in _JUST_DO_IT:
        return Intent(type="just_do_it", text=raw, channel=channel)
    if normalized in _WHERE_WAS_I:
        return Intent(type="where_was_i", text=raw, channel=channel)
    if normalized in _MUTE:
        return Intent(type="mute", text=raw, channel=channel)
    if normalized in _UNMUTE:
        return Intent(type="unmute", text=raw, channel=channel)
    if normalized in _WHO:
        return Intent(type="who_acting", text=raw, channel=channel)
    if normalized in _RESET_PREFS:
        return Intent(type="reset_preferences", text=raw, channel=channel)
    match = _RENDERER.match(normalized)
    if match:
        return Intent(type="set_renderer", text=raw, channel=channel, value=_RENDERERS[match.group("r")])
    match = _FORGET.match(normalized)
    if match:
        return Intent(type="forget_constraint", text=raw, channel=channel, value=(match.group("n") or "").strip() or None)
    match = _LIMIT.match(normalized)
    if match:
        count = "3" if match.group("n") in {"3", "three"} else "5"
        return Intent(type="set_preference", text=raw, channel=channel, field="branch_limit", value=count)
    if normalized in _FEWER:
        return Intent(type="set_preference", text=raw, channel=channel, field="branch_limit", value="3")
    if normalized in _MOREOPT:
        return Intent(type="set_preference", text=raw, channel=channel, field="branch_limit", value="5")
    match = _PREFER.match(normalized)
    if match:
        word = match.group("c")
        channel_id = {"speech": "audio", "screen": "visual"}.get(word, word)
        return Intent(type="set_preference", text=raw, channel=channel, field="preferred_channels", value=channel_id)
    match = _GOAL_PREFIX.match(normalized) or _INTERRUPT_ONLY.match(normalized)
    if match:
        # The interruption word is kept in the text so the orchestrator can say the priority changed.
        return Intent(type="set_goal", text=raw, channel=channel, field="interrupt" if match.group("interrupt") else None)
    for pattern in _SAVE_PROJECT:
        match = pattern.match(normalized)
        if match:
            return Intent(type="save_project", text=raw, channel=channel, value=match.group("n").strip())
    match = _CONTINUE_PROJECT.match(normalized)
    if match and match.group("n").strip() not in {"please", "now", "reading", "goal", "plan", "the goal", "my goal", "with it"}:
        return Intent(type="continue_project", text=raw, channel=channel, value=match.group("n").strip())
    return None


_BUDGET = re.compile(r"under \$?\d+|less than \$?\d+|below \$?\d+|cheap|budget|inexpensive|affordable")


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
    if _BUDGET.search(normalized):
        found.append("budget")
    if "remote" in normalized:
        found.append("remote")
    if "large print" in normalized or "large-print" in normalized:
        found.append("large_print")
    return found
