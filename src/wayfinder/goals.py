"""Goal planning over grounded controls. Deterministic, extractive, same-origin only.

A goal ("find the hours", "request the north room", "add the talking scale to my cart") is
turned into keywords from the person's own words. Each step picks one control that the page
itself offered, scored by overlap with those keywords. Nothing is guessed from page
instructions, no link leaves the current origin (the bridge only offers same-origin links),
and a consequential step is returned like any other control, so the policy gate still asks
for a yes. This module never clicks and never reads the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from wayfinder.browser.families import (
    PageObservation,
    add_controls,
    _choices,
    entry_control,
)
from wayfinder.models import Control

TRANSACTIONAL = {"request", "reserve", "book", "apply", "buy", "order", "purchase", "checkout", "submit", "send", "register"}
FIND_VERBS = {"find", "show", "tell", "what", "when", "where", "look", "read", "get", "who", "how"}
STOPWORDS = {
    "a", "an", "the", "my", "me", "i", "to", "for", "of", "please", "want", "need", "would", "like", "id",
    "can", "you", "help", "with", "in", "on", "it", "that", "this", "is", "are", "am", "do", "does", "us",
    "goal", "actually", "priority", "new", "instead", "now", "some", "any", "out", "up", "at", "and", "or",
    "job", "role", "position", "page", "site", "about", "into", "from", "we", "be", "so", "just", "take",
    "something", "anything", "thing", "stuff", "one", "ones",
} | FIND_VERBS
LEAD_VERBS = TRANSACTIONAL | FIND_VERBS | {"add", "put", "open", "go", "review", "take", "visit"}
NAVIGATE_VERBS = {"take", "go", "open", "review", "visit"}
SYNONYMS = {
    "hour": {"open", "opening", "time", "times", "hour"},
    "open": {"hour", "opening", "hours"},
    "cost": {"price", "fee", "fees", "pay"},
    "price": {"cost", "fee"},
    "contact": {"phone", "email", "reach"},
}
PREFIX = re.compile(
    r"^(?:(?:actually|instead|new priority|my new priority is|priority)[:,]?\s+)?"
    r"(?:(?:my\s+)?goal(?: is|:)?|i(?:'d| would)? (?:want|need|like)(?: to)?|help me(?: to)?|can you(?: please)?|please)?\s*",
    re.IGNORECASE,
)
MAX_AUTO_STEPS = 6
_CART_OR_APPLY = re.compile(r"cart|checkout|basket|appl(?:y|ication)|review", re.IGNORECASE)


@dataclass
class GoalSpec:
    text: str
    verb: str
    phrase: str
    keywords: list[str]
    transactional: bool
    adds: bool
    navigate: bool = False


@dataclass
class Decision:
    kind: str  # step | answer | need_input | done | stuck
    text: str
    control: Control | None = None
    field: str = ""


def _stem(word: str) -> str:
    word = re.sub(r"[^a-z0-9$]", "", word.lower())
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [token for token in (_stem(part) for part in re.split(r"[^A-Za-z0-9$]+", text)) if token]


def parse_goal(text: str) -> GoalSpec | None:
    """Words the person said become the goal. Page text is never passed here."""
    cleaned = " ".join(text.strip().rstrip(".!?").split())
    body = PREFIX.sub("", cleaned, count=1).strip() or cleaned
    words = body.lower().split()
    verb = words[0] if words else ""
    keywords: list[str] = []
    lead = _tokens(body)[:1] if verb in LEAD_VERBS else []
    skip_first = bool(lead)
    for index, token in enumerate(_tokens(body)):
        if index == 0 and skip_first:
            continue
        if token in STOPWORDS or token in keywords:
            continue
        if verb in {"add", "put"} and token == "cart":
            continue
        keywords.append(token)
    if not keywords:
        return None
    phrase = " ".join(word for word in body.split()[1:] if word.lower() not in {"the", "a", "an", "my", "for", "to"}) or body
    return GoalSpec(
        text=body,
        verb=verb,
        phrase=phrase.strip(),
        keywords=keywords,
        transactional=verb in TRANSACTIONAL,
        adds=verb in {"add", "put"},
        navigate=verb in NAVIGATE_VERBS,
    )


def _expand(keywords: list[str]) -> set[str]:
    found = set(keywords)
    for word in keywords:
        found |= {_stem(item) for item in SYNONYMS.get(word, set())}
    return found


def overlap(keywords: list[str], text: str) -> int:
    tokens = set(_tokens(text))
    direct = sum(1 for word in keywords if word in tokens)
    if direct:
        return direct
    return 1 if tokens & _expand(keywords) else 0


def _recipe(control: Control) -> str:
    return control.semantic_target.partition("|")[2]


_ACTION_VERB = re.compile(r"^(?:open|follow|go to)\s+", re.IGNORECASE)


def control_text(observation: PageObservation, control: Control) -> str:
    kind, _, rest = _recipe(control).partition(":")
    # "Open ..." is the action word Wayfinder adds, not page text. It must not match the synonym of "hours".
    label = _ACTION_VERB.sub("", control.label)
    index = rest.partition("=")[0]
    if kind == "follow" and index.isdigit():
        link = next((item for item in observation.links if str(item.get("index")) == index), {})
        return f"{label} {link.get('context') or ''} {link.get('href') or ''}"
    if kind == "activate" and index.isdigit():
        item = next((entry for entry in observation.details if str(entry.get("index")) == index), {})
        return f"{item.get('summary') or ''} {item.get('answer') or ''} {item.get('id') or ''}"
    return label


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if len(part.strip()) > 8]


def extract_answer(observation: PageObservation, keywords: list[str]) -> str:
    """The best sentence on the page for these keywords. Instruction-like sentences are skipped."""
    pool = []
    if observation.answer:
        pool.extend(_sentences(observation.answer))
    pool.extend(_sentences(observation.fulltext))
    skip = {item.lower() for item in observation.untrusted}
    # Button, link, and question labels are controls, not facts.
    labels = {
        " ".join(str(item.get("name") or item.get("summary") or "").split()).lower()
        for group in (observation.buttons, observation.links, observation.details)
        for item in group
    }
    skip |= {label for label in labels if label}
    best, best_score = "", 0
    need = 1 if len(keywords) == 1 else 2
    for sentence in pool:
        if sentence.lower() in skip or any(sentence.lower().startswith(item[:40]) for item in skip):
            continue
        # The heading restates the topic. Prefer a sentence that says something about it.
        if sentence.strip() == observation.h1:
            continue
        score = overlap(keywords, sentence)
        if score >= need and score > best_score:
            best, best_score = sentence, score
    return best[:240]


def _field_items(observation: PageObservation) -> list[dict]:
    return [
        item for item in observation.fields
        if not item.get("sensitive") and not item.get("hidden")
        and item.get("type") not in {"hidden", "submit", "button", "checkbox", "radio", "search", "textarea", "file", "image", "reset"}
    ]


def _form_step(spec: GoalSpec, observation: PageObservation, controls: list[Control]) -> Decision | None:
    for item in _field_items(observation):
        label = str(item.get("name"))
        if item.get("type") == "select":
            current = str(item.get("value") or "")
            wanted = next(
                (option for option in _choices(item) if overlap(spec.keywords, str(option.get("label"))) >= 1),
                None,
            )
            if wanted is not None and str(wanted.get("label")) != current:
                control = entry_control(observation, label, str(wanted.get("label")))
                if control is not None:
                    return Decision("step", f"Choose {wanted.get('label')} for {label}.", control)
            continue
        if str(item.get("value") or "").strip():
            continue
        if overlap(spec.keywords, label) >= 1:
            value = spec.phrase[:1].upper() + spec.phrase[1:]
            control = entry_control(observation, label, value)
            if control is not None:
                return Decision("step", f"Fill {label} with {value}.", control)
    for item in _field_items(observation):
        if item.get("type") != "select" and not str(item.get("value") or "").strip():
            label = str(item.get("name"))
            return Decision(
                "need_input",
                f"I need {label} from you. I will not make it up. Say: set {label.lower()} to, then the value.",
                field=label,
            )
    submit = next((control for control in controls if control.capability == "browser.submit"), None)
    if submit is not None:
        return Decision("step", f"{submit.label}. This needs your confirmation.", submit)
    return None


def decide(spec: GoalSpec, observation: PageObservation, controls: list[Control], state: dict) -> Decision:
    """Pick the next grounded step, or say why there is none. Pure: no clicking, no network."""
    visited = set(state.get("visited") or [])
    if not observation.supported or observation.blocker:
        return Decision("stuck", "This page is not one I can act on, so I stopped. Nothing was changed.")
    if spec.adds and state.get("added"):
        return Decision("done", "The item is in the demo cart. Nothing was paid.")
    find_first = spec.verb in FIND_VERBS
    if find_first:
        answer = extract_answer(observation, spec.keywords)
        if answer:
            return Decision("answer", answer)

    if spec.adds or spec.transactional:
        adds = [
            (overlap(spec.keywords, control.label), control) for control in add_controls(observation)
        ]
        adds = [item for item in adds if item[0] > 0]
        if adds and not state.get("added"):
            adds.sort(key=lambda item: -item[0])
            return Decision("step", f"Add {adds[0][1].label.replace('Add ', '', 1)}", adds[0][1])
    has_form = observation.base_family == "form" or observation.family == "form" or any(
        control.capability == "browser.submit" for control in controls
    )
    if spec.transactional and has_form:
        step = _form_step(spec, observation, controls)
        if step is not None:
            return step
    title_hit = overlap(spec.keywords, f"{observation.h1} {observation.title}") > 0
    best: tuple[int, Control] | None = None
    for control in controls:
        recipe = _recipe(control)
        if control.capability == "browser.navigate":
            url_key = next(
                (str(item.get("abs")) for item in observation.links
                 if recipe.startswith("follow:") and str(item.get("index")) == recipe.partition(":")[2]),
                "",
            )
            if url_key and url_key in visited:
                continue
            score = overlap(spec.keywords, control_text(observation, control))
            if recipe.startswith("follow:") and _CART_OR_APPLY.search(control.label) and (
                (spec.transactional or spec.adds) and (state.get("added") or title_hit)
            ):
                score += 2
            if score and (best is None or score > best[0]):
                best = (score, control)
        elif (
            control.capability == "browser.filter" and recipe.startswith("filter:") and not spec.adds
            # An add button is only ever pressed for a goal that says add. "take me to the cart" must not add anything.
            and not control.id.startswith("add-")
        ):
            score = overlap(spec.keywords, control.label)
            if score and (best is None or score > best[0]) and control.id not in set(state.get("used") or []):
                best = (score, control)
    if best is not None:
        return Decision("step", f"{best[1].label}.", best[1])
    if any(item.get("type") == "search" for item in observation.fields) and not state.get("searched"):
        control = entry_control(observation, "search", " ".join(spec.keywords))
        if control is not None:
            return Decision("step", f"Search this site for {' '.join(spec.keywords)}.", control)
    if not find_first and not spec.adds and not spec.transactional:
        answer = extract_answer(observation, spec.keywords)
        if answer:
            return Decision("answer", answer)
    if state.get("steps_done"):
        return Decision(
            "done",
            "That is as far as this page goes toward your goal. Choose a number, or give me a new goal.",
        )
    return Decision(
        "stuck",
        "I could not find a step on this site that matches your goal, and I will not leave this site to look. "
        "You can say a number, or give me a different goal.",
    )
