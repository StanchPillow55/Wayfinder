"""Deterministic policy. A model cannot widen this."""

from __future__ import annotations

from wayfinder.models import Control, PolicyDecision


def decide(control: Control, *, confirmed: bool) -> PolicyDecision:
    permissions = _permissions(control.capability)
    if control.risk_class == "irreversible":
        return PolicyDecision(
            decision="DENY",
            risk_class=control.risk_class,
            permissions=permissions,
            reason="Irreversible actions are denied in this demo.",
        )
    if control.risk_class == "consequential" and not confirmed:
        return PolicyDecision(
            decision="REQUIRE_CONFIRMATION",
            risk_class=control.risk_class,
            permissions=permissions,
            reason="Consequential actions wait for an explicit yes.",
        )
    if control.risk_class == "consequential" and confirmed:
        return PolicyDecision(
            decision="ALLOW",
            risk_class=control.risk_class,
            permissions=permissions,
            reason="The user confirmed this consequential action.",
        )
    return PolicyDecision(
        decision="ALLOW",
        risk_class=control.risk_class,
        permissions=permissions,
        reason="Read and reversible page changes are allowed.",
    )


def _permissions(capability: str) -> list[str]:
    if capability.startswith("reader."):
        return ["read"]
    if capability.startswith("browser."):
        return ["browser.write"]
    return []
