# ADR-003: Bounded live-browser MVP

- Status: Accepted
- Date: 2026-10-18
- Proposal: Wayfinder Project Proposal 2.3
- Related: ADR-001, ADR-002
- Revises: proposal 2.2 decision D-008, and the phase numbers used in proposal 2.2 and in the consequences of ADR-002

## Context

The product intent is an accessibility-first browser companion. Wayfinder should observe a webpage, turn it into a short semantic interface, offer a few meaningful actions, and let the person continue by text, voice, touch, or tactile output.

Proposal 2.2 and the current repository instead define success as one invented page, Harbor Line, driven by application-specific methods in `ControlledSiteExecutor`. Live browser ingestion, Playwright, Browser Use, and a browser extension are listed as future adapters that the release candidate must not include. The phase plan then spends the next milestones on a DevKit acknowledgement and a servo.

That fixture is useful. It is not the product. A hackathon cannot promise the entire Internet, and a single fake transit page cannot prove a browser companion.

ADR-001 and ADR-002 stay in force. The Mac still owns canonical state. WIR `0.2` and device protocol `0.1` stay frozen. This ADR changes product scope and phase order only.

## Decision

Wayfinder’s software MVP is a bounded live-browser companion:

1. Read the active page through a real browser adapter.
2. Extract stable DOM or accessibility-tree evidence.
3. Explain what the page is and what the person can do.
4. Present three to five grounded actions.
5. Execute supported actions through Playwright, Browser Use, or a browser extension.
6. Re-read the page and verify the result.
7. Render the same semantic state across available modalities.
8. Fall back honestly when a site or action is unsupported.

The demo supports two or three page patterns: an FAQ or documentation page, a search or results page, and a form or task page. Those pages may be selected live sites, local mirrors of realistic structure, or both. The adapter is a browser boundary. Harbor Line methods are not that adapter.

Harbor Line remains only as a deterministic regression fixture, an offline fallback, a prompt-injection and failure-test environment, and a reference for contract tests. It does not define the purpose, value pitch, product scope, or MVP.

Phase order is:

| Phase | Meaning |
|---|---|
| Phase 0 | Contracts, WIR, permissions, and Mac-owned orchestration |
| Phase 1 | Deterministic Harbor Line engineering fixture |
| Phase 2 | Real-browser read-only ingestion |
| Phase 3 | Bounded live-browser interaction and independent verification |
| Phase 4 | Software MVP across two or three page patterns |
| Phase 5 | Physical DevKit USB and servo integration |
| Phase 6 | BOX-3 display and touch |
| Phase 7 | Voice, perception, and broader website generalization |

The next product milestone is Phase 2. More Harbor Line behavior is not product progress. Hardware work may continue only when it does not delay Phases 2 through 4. The fixture must keep running with no board, no live site, and no model.

## Consequences

Proposal 2.3 was the product source of truth when this ADR was accepted. Proposal 2.4 (ADR-004) now supersedes it, and `/api/health` reports `2.4`.

ADR-002’s sentences that call the physical DevKit acknowledgement “Phase 2” and the servo path “Phase 3 and Phase 4” describe proposal 2.2’s hardware sequence. Under this ADR that work is Phase 5. The wire freeze in ADR-002 is unchanged.

At acceptance the repository had no live-browser adapter. Phases 2 through 4 now exist in software (Playwright), so demos lead with the browser pages and show Harbor Line only as the labeled fixture.
