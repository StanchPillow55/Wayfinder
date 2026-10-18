"""Hands-free input grammar and vision-event mapping. No permissions, no tools, no model.

This module is the tested reference for two browser demos that mirror it:
``apps/web/voice.js`` (spoken commands) and ``apps/web/vision.js`` (laptop-camera
selector, and a future P4X-EYE ``vision.event``). Nothing here clicks, submits, or
reads an image. It only turns already-recognized words, or an already-typed device
event, into a small command the existing session API will gate.

Rules that hold on both input paths:

- Confirmation is never skipped. A spoken command or a clearly large camera
  motion can choose an option. Whether that option needs confirmation is decided
  by the session. The word ``confirm``, or a second separate select signal,
  confirms. Becoming still after an expression is not a selection.
- Text that looks like an instruction aimed at the assistant is rejected, never run.
- A P4X-EYE ``scene_summary`` is untrusted free text. It maps to no action.
- No frame, image, or media field is read or kept here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Spoken target words -> the session target ids the web app already opens.
TARGET_WORDS: dict[str, str] = {
    "harbor": "fixture",
    "harbor line": "fixture",
    "fixture": "fixture",
    "faq": "faq",
    "library help": "faq",
    "library": "faq",
    "search": "search",
    "city services": "search",
    "form": "form",
    "room form": "form",
    "room request": "form",
    "shop": "shop",
    "store": "shop",
    "shopping": "shop",
    "commerce": "shop",
    "jobs": "jobs",
    "careers": "jobs",
    "job board": "jobs",
    "unlabeled": "unlabeled",
    "help desk": "unlabeled",
    "repair": "unlabeled",
}

_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}

_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "ignore your instructions",
    "disregard",
    "system prompt",
    "you are now",
    "new instructions",
)

_STOP = {"stop", "stop talking", "be quiet", "quiet", "silence", "hush", "cancel", "never mind", "nevermind"}
_HELP = {"help", "what can i say", "commands", "list commands"}
_DESCRIBE = {"what is here", "whats here", "where am i", "what can i do", "what can i do here"}
_NEXT = {"next", "next option", "next one"}
_PREVIOUS = {"previous", "previous option", "previous one"}
_BACK = {"back", "go back"}
_CONFIRM = {"confirm", "select", "select this", "choose this", "choose it", "this one", "open this"}
_YES = {"yes", "do it", "go ahead", "confirmed"}
_NO = {"no", "nope", "deny", "dont", "do not"}
_READ = {"read", "read it", "read this", "read page", "read aloud", "read again", "repeat"}
_PASS_THROUGH = {
    "less detail", "more detail", "explain", "compare", "continue",
    # Interruptions and memory. The session decides what each one does.
    "wait", "hold on", "pause", "what did you just say", "say that again", "explain that",
    "read everything", "forget that constraint", "just do it for me", "just do it",
    "where was i", "who is acting", "mute", "unmute", "mute audio", "unmute audio",
    "reset my preferences", "fewer options", "more options",
    "switch to visual", "switch to audio", "switch to braille", "switch to tactile", "switch to hands free",
}
_GOAL = (
    re.compile(r"^(?:actually|instead|new priority)\b.+$"),
    re.compile(r"^(?:my goal is|goal|i want to|i need to|i want|i need|i would like to|id like to|help me)\b.+$"),
    re.compile(r"^(?:request|reserve|book|apply for|buy|order|purchase|add|take me|review)\b.+$"),
)
_PROJECT = (
    re.compile(r"^(?:save|name)(?: this| my| the)?(?: project| workspace| search| task)? (?:as|called|named) [a-z0-9 ]{2,60}$"),
    re.compile(r"^(?:continue|resume|reopen|pick up)(?: with)?(?: my| the)?(?: project| workspace)? [a-z0-9 ]{2,60}$"),
)
_FORGET = re.compile(r"^forget (?:the |my )?[a-z$0-9 -]{2,40} constraint$")
_OPEN_ADDRESS = {"open address", "open the address", "open the typed address", "open the address i typed", "go to the address", "go to the typed address"}
_URL_OPEN = re.compile(
    r"^(?:open|go to|visit|load)(?: the)?(?: website| site| address| url| page)? (?P<u>[a-z0-9][a-z0-9 .\-/]*)$"
)
_URL_SHAPE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[a-z0-9._~%/-]*)?$")

_ENTER = (
    re.compile(r"^(?:search|find|look up)(?: for)? .+$"),
    re.compile(r"^(?:set|fill in|fill|enter|type|put) .+ (?:to|with|as) .+$"),
)
_OPEN = re.compile(r"^(?:open|go to|show|load)(?: the)? (?P<t>[a-z ]+?)(?: page)?$")
_NUMBER = re.compile(
    r"^(?:(?:do|choose|pick|press) )?(?:(?:number|option) )?(?P<n>[1-5]|one|two|three|four|five)$"
)


@dataclass(frozen=True)
class VoiceCommand:
    """What a spoken phrase means.

    kind is one of: stop, help, describe, next, previous, back, confirm, yes, no,
    read, enter, goal, open, open_url, open_address, number, command, rejected, unknown.
    ``text`` is the normalized phrase to POST for kinds that go to the session
    (describe, back, no, enter, command). ``target`` is a session target for open.
    """

    kind: str
    text: str = ""
    target: str | None = None
    number: int | None = None
    url: str | None = None


def normalize(raw: str) -> str:
    lowered = raw.strip().lower().replace("’", "'")
    lowered = lowered.replace("'", "")
    lowered = re.sub(r"[?!.,;:]+", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def spoken_url(raw: str) -> str | None:
    """'open example dot org slash docs' or 'open example.org' -> 'https://example.org/docs'.

    Only a plain host-shaped word list with a dot becomes an address. The web layer still
    runs it through the same http(s) check as a typed address.
    """
    lowered = raw.strip().lower()
    match = _URL_OPEN.match(lowered)
    if not match:
        return None
    text = match.group("u").strip()
    text = re.sub(r"\s+dot\s+", ".", text)
    text = re.sub(r"\s+slash\s+", "/", text)
    text = re.sub(r"\s*\.\s*", ".", text).replace(" ", "")
    if not _URL_SHAPE.match(text):
        return None
    return "https://" + text


def is_injection(normalized: str) -> bool:
    return any(marker in normalized for marker in _INJECTION_MARKERS)


def parse_voice(raw: str) -> VoiceCommand:
    n = normalize(raw)
    if not n:
        return VoiceCommand("unknown")
    if is_injection(n):
        return VoiceCommand("rejected", text=n[:80])
    if n in _STOP or n.startswith("stop "):
        return VoiceCommand("stop", text="stop")
    if n in _HELP:
        return VoiceCommand("help")
    if n in _DESCRIBE or n.startswith("what is here"):
        return VoiceCommand("describe", text="what is here")
    if n in _NEXT:
        return VoiceCommand("next")
    if n in _PREVIOUS:
        return VoiceCommand("previous")
    if n in _BACK:
        return VoiceCommand("back", text="go back")
    if n in _CONFIRM:
        return VoiceCommand("confirm", text=n)
    if n in _YES:
        return VoiceCommand("yes", text=n)
    if n in _NO:
        return VoiceCommand("no", text="no")
    if n in _READ:
        return VoiceCommand("read")
    if n in _PASS_THROUGH:
        return VoiceCommand("command", text=n)
    opened = _OPEN.match(n)
    if opened:
        target = TARGET_WORDS.get(opened.group("t").strip())
        if target:
            return VoiceCommand("open", target=target)
    if n in _OPEN_ADDRESS:
        return VoiceCommand("open_address")
    address = spoken_url(raw)
    if address:
        return VoiceCommand("open_url", url=address)
    if any(pattern.match(n) for pattern in _PROJECT) or _FORGET.match(n):
        return VoiceCommand("command", text=n[:120])
    if any(pattern.match(n) for pattern in _GOAL):
        return VoiceCommand("goal", text=n[:200])
    if any(pattern.match(n) for pattern in _ENTER):
        return VoiceCommand("enter", text=n[:200])
    number = _NUMBER.match(n)
    if number:
        return VoiceCommand("number", number=_NUMBERS[number.group("n")])
    return VoiceCommand("unknown", text=n[:80])


@dataclass(frozen=True)
class Plan:
    """What the page controller should do. ``post`` is a command text, ``action_id`` a branch."""

    kind: str  # move, select, confirm, decline, arm, speak, none
    cursor: int = 0
    action_id: str | None = None
    post: str | None = None
    note: str = ""


def plan_signal(
    signal: str,
    *,
    cursor: int,
    branch_ids: list[str],
    pending: bool,
    recent_next: bool = False,
) -> Plan:
    """Map a camera/device signal (``next`` or ``select``) onto the session.

    ``select`` on a waiting confirmation is the second, separate signal and posts
    ``yes``. ``select`` otherwise chooses the current option. The session decides
    whether that option needs confirmation. While a confirmation waits, one
    ``next`` only arms the next ``select``; two ``next`` signals close together
    decline. A signal never confirms by itself.
    """
    count = len(branch_ids)
    if signal == "next":
        if pending:
            if recent_next:
                return Plan("decline", cursor=cursor, post="no", note="Declined.")
            return Plan(
                "arm",
                cursor=cursor,
                note="Confirmation waiting. A long deliberate move confirms, or move twice to decline.",
            )
        if count == 0:
            return Plan("none", note="No options to move through.")
        return Plan("move", cursor=(cursor + 1) % count)
    if signal == "select":
        if pending:
            return Plan("confirm", cursor=cursor, post="yes", note="Second signal. Confirming.")
        if count == 0:
            return Plan("none", note="No option is selected.")
        index = cursor % count
        return Plan("select", cursor=index, action_id=branch_ids[index])
    return Plan("none", note="Unknown signal.")


def plan_voice(
    command: VoiceCommand,
    *,
    cursor: int,
    branch_ids: list[str],
    pending: bool,
) -> Plan:
    """Map a parsed voice command that needs local state. Others go straight to the session."""
    count = len(branch_ids)
    if command.kind == "next":
        if pending:
            return Plan("speak", cursor=cursor, note="A confirmation is waiting. Say confirm, or say no.")
        if count == 0:
            return Plan("none", note="No options to move through.")
        return Plan("move", cursor=(cursor + 1) % count)
    if command.kind == "previous":
        if pending:
            return Plan("speak", cursor=cursor, note="A confirmation is waiting. Say confirm, or say no.")
        if count == 0:
            return Plan("none", note="No options to move through.")
        return Plan("move", cursor=(cursor - 1) % count)
    if command.kind in {"confirm", "yes"}:
        if pending:
            return Plan("confirm", cursor=cursor, post="yes")
        if command.kind == "yes":
            return Plan("speak", cursor=cursor, note="Nothing is waiting for confirmation. Say confirm to choose the current option.")
        if count == 0:
            return Plan("none", note="No option is selected.")
        index = cursor % count
        return Plan("select", cursor=index, action_id=branch_ids[index])
    if command.kind == "number":
        number = command.number or 0
        if pending:
            return Plan("speak", cursor=cursor, note="A confirmation is waiting. Say confirm, or say no.")
        if not 1 <= number <= count:
            return Plan("speak", cursor=cursor, note=f"There is no option {number}. Say a number from 1 to {count}.")
        return Plan("select", cursor=number - 1, action_id=branch_ids[number - 1])
    return Plan("none")


# --- Vision events (a future P4X-EYE, or a laptop-camera stand-in) -------------

STEADY_EVENTS_TO_SELECT = 3


@dataclass
class VisionMapper:
    """Dwell-by-event-count for a ``vision.event`` stream. Holds no media.

    ``scene_changed`` -> ``next`` and re-arms. ``user_present`` is not intent by
    itself; ``STEADY_EVENTS_TO_SELECT`` in a row, after a ``scene_changed`` armed
    the mapper, -> one ``select``, then it must be re-armed. ``scene_summary`` is
    untrusted text and maps to nothing.
    """

    armed: bool = False
    steady: int = 0
    reasons: list[str] = field(default_factory=list)

    def feed(self, payload: dict) -> str | None:
        event = payload.get("event")
        if event == "scene_changed":
            self.armed = True
            self.steady = 0
            return "next"
        if event == "user_present":
            if not self.armed:
                return None
            self.steady += 1
            if self.steady >= STEADY_EVENTS_TO_SELECT:
                self.armed = False
                self.steady = 0
                return "select"
            return None
        # scene_summary and anything unknown: evidence only. Summary text is never an action.
        return None


def vision_event_signal(payload: dict, mapper: VisionMapper) -> str | None:
    """Return ``next``, ``select``, or None for one device ``vision.event`` payload."""
    return mapper.feed(payload)
