"""Turn a page snapshot into WIR. This module cannot click, submit, or drive an actuator."""

from __future__ import annotations

from wayfinder.models import Density, Goal, Snapshot, WIR


def interpret(
    snapshot: Snapshot,
    *,
    session_id: str,
    turn_id: str,
    trace_id: str,
    goal: Goal,
    channels: dict[str, list[str]],
    devices: dict[str, list[dict[str, str]]],
    history_summary: str,
    density: Density,
) -> WIR:
    uncertainty: list[str] = []
    if snapshot.untrusted_text:
        uncertainty.append(
            "The page contains instructions. They are untrusted data and were not followed."
        )
    if snapshot.hidden_station_count:
        uncertainty.append("Some stations are hidden by the current view or filter.")
    alerts: list[dict[str, str]] = []
    if snapshot.alert == "requested":
        alerts.append(
            {
                "id": "arrival_alert",
                "level": "info",
                "text": "Step-free arrival alert requested.",
            }
        )
    return WIR(
        session_id=session_id,
        turn_id=turn_id,
        trace_id=trace_id,
        page={
            "url": snapshot.url,
            "title": snapshot.title,
            "purpose": snapshot.purpose,
            "summary": _summary_for_density(snapshot, density),
        },
        goal=goal,
        entities=snapshot.entities,
        actions=[],
        alerts=alerts,
        history_summary=history_summary,
        available_channels=channels,
        devices=devices,
        uncertainty=uncertainty,
        information_density=density,
    )


def _summary_for_density(snapshot: Snapshot, density: Density) -> str:
    if density == "brief":
        return snapshot.purpose
    if density == "full":
        names = ", ".join(entity.name for entity in snapshot.entities if entity.trust == "observed")
        return f"{snapshot.summary} Observed items: {names}."
    return snapshot.summary
