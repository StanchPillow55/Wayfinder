"""Grounded page writes for the controlled Harbor Line site.

The returned status is not verification. Call wayfinder.verifier after this.
"""

from __future__ import annotations

from wayfinder.demo_site import apply_control, build_snapshot
from wayfinder.models import Control, ExecResult, PageState


def execute(page: PageState, control: Control, faults: set[str]) -> tuple[PageState, ExecResult]:
    known = {item.id for item in build_snapshot(page).controls}
    if control.id not in known or "selector_failure" in faults:
        return page, ExecResult(status="rejected", error_class="selector_not_found", changed=False)
    if control.capability.startswith("reader."):
        return page, ExecResult(status="unchanged", changed=False)
    if "verification_mismatch" in faults:
        # Claim a write without producing the postcondition. The verifier must fail.
        return page, ExecResult(status="applied", changed=False)
    if not control.capability.startswith("browser."):
        return page, ExecResult(status="rejected", error_class="selector_not_found", changed=False)
    updated = apply_control(page, control.id)
    changed = updated.model_dump() != page.model_dump()
    return updated, ExecResult(status="applied", changed=changed)
