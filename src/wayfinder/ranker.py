"""Rank grounded controls. Never invent a capability that is not on the page."""

from __future__ import annotations

from wayfinder.models import Control, Density


def rank(controls: list[Control], constraints: list[str], density: Density) -> list[Control]:
    limit = 3 if density == "brief" else 5
    boosted: list[tuple[int, int, Control]] = []
    for index, control in enumerate(controls):
        score = control.priority
        if "evening" in constraints and control.id in {"show_evening"}:
            score += 40
        if "step_free" in constraints and control.id in {"filter_step_free"}:
            score += 40
        if "alert" in constraints and control.id == "request_alert":
            score += 40
        boosted.append((score, -index, control))
    boosted.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [control for _, _, control in boosted[:limit]]
