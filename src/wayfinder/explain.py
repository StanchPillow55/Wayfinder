"""Extractive semantic explanation of a page: purpose, landmarks, choices.

Everything said here comes from evidence the adapter already collected (title, headings,
landmark elements, grounded controls). No model, no network, and no guess about what the
page "really" means. When a page has no landmarks and no headings, this says so plainly.
"""

from __future__ import annotations

from wayfinder.browser.families import PageObservation
from wayfinder.models import Control

ROLE_WORDS = {
    "banner": "header",
    "navigation": "navigation",
    "main": "main content",
    "complementary": "sidebar",
    "contentinfo": "footer",
    "search": "search",
    "form": "form",
    "region": "labelled region",
}
FAMILY_WORDS = {
    "faq": "a list of questions you can open",
    "search": "a search page with results",
    "form": "a form",
    "docs": "a content page that links onward",
    "commerce": "a local shop mirror page",
    "jobs": "a local jobs mirror page",
}


def _landmark_text(observation: PageObservation) -> str:
    counts: dict[str, int] = {}
    labels: list[str] = []
    for item in observation.landmarks:
        role = ROLE_WORDS.get(str(item.get("role")), str(item.get("role")))
        counts[role] = counts.get(role, 0) + 1
        label = str(item.get("label") or "")
        if label and role not in {"main content"} and len(labels) < 4:
            labels.append(f"{role} called {label}")
    parts = [f"{count} {role}" if count > 1 else f"a {role}" for role, count in counts.items()]
    text = ", ".join(parts)
    if labels:
        text += f" ({'; '.join(labels)})"
    return text


def semantic_explanation(
    observation: PageObservation,
    controls: list[Control],
    density: str = "standard",
) -> dict:
    """Return the explanation text and the structure it was built from."""
    headings = [str(item.get("text")) for item in observation.headings if item.get("text")]
    sections = [
        str(item.get("text")) for item in observation.headings
        if str(item.get("level", "")).lower() in {"h2", "h3"} and item.get("text")
    ]
    choices = [control.label for control in controls if control.capability != "reader.explain"]
    has_semantics = bool(observation.landmarks or headings)
    purpose = f"{observation.h1}."
    if observation.purpose and observation.purpose != observation.h1:
        purpose += f" {observation.purpose.rstrip('.')}."
    family = FAMILY_WORDS.get(observation.family)
    evidence = ["document.title"]
    evidence.extend(f"heading:{item.get('index')}" for item in observation.headings[:4])
    evidence.extend(f"landmark:{item.get('role')}" for item in observation.landmarks[:4])

    if not has_semantics:
        counts = (
            f"{len([x for x in observation.links if not x.get('hidden')])} links, "
            f"{len(observation.buttons)} buttons, and {len(observation.fields)} fields"
        )
        text = (
            f"This page has no landmarks and no headings, so I cannot describe its structure. "
            f"All I can read is its title, {observation.title or observation.h1}, and {counts}. "
            f"{purpose}"
        )
        if choices and density != "brief":
            text += f" Choices I can ground: {'; '.join(choices[:5])}."
    elif density == "brief":
        text = f"{purpose} {len(choices)} choices."
        if family:
            text = f"{purpose} It looks like {family}. {len(choices)} choices."
    else:
        text = purpose
        if family:
            text += f" It looks like {family}."
        landmark_text = _landmark_text(observation)
        if landmark_text:
            text += f" Landmarks: {landmark_text}."
        else:
            text += " It has headings but no landmark regions."
        if density == "full" and sections:
            text += f" Sections: {', '.join(sections[:6])}."
        if choices:
            text += f" Choices: {'; '.join(choices[:5])}."
        if density == "full":
            text += " This comes only from the page's own structure and the controls I can ground."
    if observation.untrusted:
        text += " This page contains an instruction-like sentence. It is page text, so I did not follow it."
    return {
        "text": text.strip(),
        "has_semantics": has_semantics,
        "purpose": purpose.strip(),
        "family": observation.family,
        "landmarks": [
            {"role": str(item.get("role")), "label": str(item.get("label") or "")}
            for item in observation.landmarks[:10]
        ],
        "sections": sections[:8],
        "choices": choices[:5],
        "evidence": evidence[:10],
        "limit": (
            "" if has_semantics
            else "No landmarks or headings were found, so the structure could not be described."
        ),
    }
