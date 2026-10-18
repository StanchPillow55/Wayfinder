"""Local capability registry (the "marketplace"). Not a store, not a sponsor SDK, no network.

Each entry says what a capability may do (permissions), how far it is trusted (trust), and
whether it is available in this session. The router picks an entry for every turn and the
UI shows the choice ("Browser Navigator selected"). Trust is separated in code:

- reader / page interpreter: read-only. It builds state and cannot click or submit.
- orchestrator: routes a request to an entry. It never touches the page and never clicks
  coordinates; only the navigator runs a grounded recipe.
- policy gate: returns allow, confirm, or deny. It runs nothing.
- browser navigator: executes one grounded action (an id on the freshly read page). It
  rejects anything it was not offered.
- verifier: read-only. It re-reads and reports success, partial, failure, or ambiguous.

Adding a specialist (for example a commerce or jobs agent) is one more entry here plus a
planner registered in browser/families.py. It sits behind the same router and policy gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Trust = str  # read_only | routes_only | decides | executes_grounded | plans_read_only


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    role: str
    trust: Trust
    permissions: tuple[str, ...]
    router_capabilities: tuple[str, ...] = ()
    kind: str = "core"  # core entries are locked on; specialists can be turned off
    scope: str = "Local only. No network service, no sponsor SDK."
    try_it: str = ""
    family: str = ""
    extra: dict = field(default_factory=dict)


REGISTRY: dict[str, Capability] = {
    entry.id: entry
    for entry in (
        Capability(
            "reader", "Reader", "Page interpreter. Turns a page into semantic state.", "read_only", ("read",),
            ("reader.summarize", "reader.explain", "reader.compare", "reader.topics", "reader.answer", "reader.fields"),
            try_it="what is here",
        ),
        Capability(
            "branch_planner", "Branch Planner", "Ranks 3 to 5 grounded choices from the page.", "read_only", ("read",),
            try_it="what can i do",
        ),
        Capability(
            "orchestrator", "Orchestrator", "Routes a request to one capability. Cannot click or type.", "routes_only", (),
            try_it="who is acting",
        ),
        Capability(
            "browser_navigator", "Browser Navigator", "Runs one grounded action on the page, only if the page offered it.",
            "executes_grounded", ("browser.write",), ("browser.navigate", "browser.filter", "browser.fill", "browser.submit"),
            try_it="do number 2",
        ),
        Capability(
            "policy_gate", "Policy Gate", "Answers allow, confirm, or deny for each action. Runs nothing.", "decides", (),
            try_it="what needs confirmation",
        ),
        Capability(
            "verifier", "Verifier", "Re-reads the page afterward and says success, partial, failure, or ambiguous.",
            "read_only", ("read",), try_it="what did you just say",
        ),
        Capability(
            "goal_planner", "Goal Planner", "Plans grounded same-origin steps toward a stated outcome. Stops for confirmation.",
            "plans_read_only", ("read",), kind="specialist", try_it="find the hours",
        ),
        Capability(
            "semantic_explainer", "Semantic Explainer", "Explains purpose, landmarks, and choices from page evidence only.",
            "read_only", ("read",), kind="specialist", try_it="explain",
        ),
        Capability(
            "repair_agent", "Accessibility Repair", "Suggests a name for an unnamed control from nearby text, marked inferred.",
            "read_only", ("read",), kind="specialist", try_it="open unlabeled",
        ),
        Capability(
            "memory_agent", "Memory", "Session path, named projects, and a preference bank. Local only.",
            "read_only", (), kind="specialist", try_it="where was i",
        ),
        Capability(
            "commerce_agent", "Commerce Agent", "Local shop mirror: browse, compare, add, review. No payment.",
            "executes_grounded", ("browser.write",), kind="specialist", family="commerce", try_it="open shop",
        ),
        Capability(
            "jobs_agent", "Jobs Agent", "Local jobs mirror: search roles, open one, review an application. Nothing is sent.",
            "executes_grounded", ("browser.write",), kind="specialist", family="jobs", try_it="open jobs",
        ),
    )
}

# Router agent names used by router.REGISTRY map onto these entries.
AGENT_TO_ID = {"reader": "reader", "browser_navigator": "browser_navigator"}
FAMILY_TO_ID = {entry.family: entry.id for entry in REGISTRY.values() if entry.family}
# Specialists whose "off" state changes behavior elsewhere.
SPECIALIST_IDS = tuple(entry.id for entry in REGISTRY.values() if entry.kind == "specialist")


class UnknownCapabilityId(ValueError):
    pass


def get(capability_id: str) -> Capability:
    try:
        return REGISTRY[capability_id]
    except KeyError as exc:
        raise UnknownCapabilityId(capability_id) from exc


def is_enabled(off: set[str], capability_id: str) -> bool:
    entry = get(capability_id)
    return entry.kind == "core" or capability_id not in off


def specialist_for_family(family: str) -> str | None:
    return FAMILY_TO_ID.get(family)


def selection(capability_id: str, reason: str, via: str | None = None) -> dict:
    """What the router chose, in words the UI and the speech line can both show."""
    entry = get(capability_id)
    line = f"{entry.name} selected"
    if via:
        line += f" (through {get(via).name})"
    if reason:
        line += f": {reason}"
    return {"id": entry.id, "name": entry.name, "trust": entry.trust, "reason": reason, "via": via, "line": line}


def registry_view(off: set[str], selected_ids: list[str], selected_now: dict | None) -> list[dict]:
    """The visible list. 'acting' marks entries that took part in the latest turn."""
    items = []
    for entry in REGISTRY.values():
        enabled = is_enabled(off, entry.id)
        items.append(
            {
                "id": entry.id,
                "name": entry.name,
                "role": entry.role,
                "kind": entry.kind,
                "trust": entry.trust,
                "permissions": list(entry.permissions),
                "capabilities": list(entry.router_capabilities),
                "availability": "available" if enabled else "off",
                "enabled": enabled,
                "locked": entry.kind == "core",
                "acting": entry.id in selected_ids,
                "selected": bool(selected_now and selected_now.get("id") == entry.id),
                "scope": entry.scope,
                "try_it": entry.try_it,
            }
        )
    return items
