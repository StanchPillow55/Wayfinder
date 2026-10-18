# ADR-002: Freeze WIR 0.2 and device protocol 0.1

- Status: Accepted
- Date: 2026-10-17
- Proposal: Wayfinder Hardware Integrated Project Proposal 2.2
- Related: ADR-001

## Context

Proposal 2.1 is the product source of truth. An editing pass of that document showed draft envelope numbers that the running software does not use: device protocol `0.2`, WIR `0.3`, source `wayfinder_mac`, and board IDs `box3_surface` and `p4x_vision`.

The Mac, the simulator, the tests, and the DevKit firmware already agree on protocol `0.1`, WIR `0.2`, and these endpoint IDs:

- `mac_orchestrator`
- `devkit_tactile`
- `esp32_s3_box3`
- `p4_eye`

A protocol migration now would change the software release candidate without new product behavior. The review of v2.1 asked for the document to match the implementation unless a migration was intentional.

## Decision

WIR `0.2` and device protocol `0.1` stay frozen. Other protocol versions are rejected. The endpoint IDs above stay frozen. `esp32_c3_display`, `esp32_s3_gateway`, and `uart` stay rejected under ADR-001.

Harbor Line remains a controlled fixture. Reader and Browser Navigator remain deterministic capabilities. Structured traces stay in-process. Sentry is not part of this contract.

Sequence deduplication and persistent actuator lockout are part of the software contract now, so the simulator and the DevKit firmware can be tested before a physical ACK exists. A duplicate tactile sequence does not move the output. Lockout blocks later renders until an explicit enable. Detach after stop does not lock the actuator.

## Consequences

Proposal 2.2 decision D-013 confirms protocol `0.1`. Proposal 2.3 uses the versions and endpoint IDs in this ADR in its examples. Older drafts that showed WIR `0.3`, source `wayfinder_mac`, ids `box3_surface` and `p4x_vision`, pose names such as `confirmation_required`, or agent `message_version` `0.3` were not a migration. The running envelopes stay on the versions and ids in this ADR, and on the pose tokens `cleared`, `orient`, `branch_ready`, `needs_confirmation`, `action_verified`, and `failure`.

Phase 2 is still incomplete until a physical DevKit returns a typed ACK. An in-process serial loopback may exercise port override, reconnect, malformed input, protocol rejection, and the size limit. A loopback acknowledgement is not that physical ACK. Phase 3 and Phase 4 stay incomplete until the servo path is measured on the bench.

Those phase numbers are the proposal 2.2 hardware sequence. [ADR-003](0003-bounded-live-browser-mvp.md) renumbers that work as Phase 5 and makes real-browser ingestion the next product milestone. This ADR’s wire freeze is unchanged.
