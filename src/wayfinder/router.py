"""Local capability registry. Reader and Browser Navigator are the MVP pair."""

from __future__ import annotations

from wayfinder.models import Control, Route

REGISTRY: dict[str, dict[str, object]] = {
    "reader.summarize": {"agent": "reader", "permissions": ["read"]},
    "reader.explain": {"agent": "reader", "permissions": ["read"]},
    "reader.compare": {"agent": "reader", "permissions": ["read"]},
    "reader.topics": {"agent": "reader", "permissions": ["read"]},
    "reader.answer": {"agent": "reader", "permissions": ["read"]},
    "reader.fields": {"agent": "reader", "permissions": ["read"]},
    "browser.navigate": {"agent": "browser_navigator", "permissions": ["browser.write"]},
    "browser.filter": {"agent": "browser_navigator", "permissions": ["browser.write"]},
    "browser.fill": {"agent": "browser_navigator", "permissions": ["browser.write"]},
    "browser.submit": {"agent": "browser_navigator", "permissions": ["browser.write"]},
}


class UnknownCapability(ValueError):
    pass


def route(control: Control) -> Route:
    spec = REGISTRY.get(control.capability)
    if spec is None:
        raise UnknownCapability(control.capability)
    return Route(
        capability=control.capability,
        agent=spec["agent"],  # type: ignore[arg-type]
        permissions=list(spec["permissions"]),  # type: ignore[arg-type]
    )
