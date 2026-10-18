"""Rank grounded controls. Never invent a capability that is not on the page."""

from __future__ import annotations

from wayfinder.models import Control, Density

# A learned constraint boosts a control whose own label says the same thing. Labels come from
# the page, so a constraint cannot create a control.
LABEL_HINTS = {
    "budget": ("under", "cheap", "budget", "affordable"),
    "remote": ("remote",),
    "large_print": ("large-print", "large print"),
}


def rank(
    controls: list[Control],
    constraints: list[str],
    density: Density,
    limit: int | None = None,
) -> list[Control]:
    cap = 3 if density == "brief" else 5
    if limit is not None:
        cap = min(cap, max(1, limit))
    boosted: list[tuple[int, int, Control]] = []
    for index, control in enumerate(controls):
        score = control.priority
        if "evening" in constraints and control.id in {"show_evening"}:
            score += 40
        if "step_free" in constraints and control.id in {"filter_step_free"}:
            score += 40
        if "alert" in constraints and control.id == "request_alert":
            score += 40
        label = control.label.lower()
        for constraint in constraints:
            hints = LABEL_HINTS.get(constraint, ())
            if control.capability != "reader.explain" and any(hint in label for hint in hints):
                score += 40
        boosted.append((score, -index, control))
    boosted.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [control for _, _, control in boosted[:cap]]
