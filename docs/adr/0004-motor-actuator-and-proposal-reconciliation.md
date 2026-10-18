# ADR-004: Motor actuator and proposal reconciliation

- Status: Accepted
- Date: 2026-10-18
- Proposal: Wayfinder Project Proposal 2.4
- Related: ADR-001, ADR-002, ADR-003
- Supersedes: both draft proposals that were labeled Version 2.3

## Context

Two things changed after ADR-003.

**The actuator.** Proposal 2.3 and the DevKit firmware source assume a hobby servo that holds an angle. The person building Wayfinder could not get a servo. The actuator on hand is a 130 mini DC brushless motor. It spins. It does not hold a commanded angle, and the software has no feedback from it.

**Two competing 2.3 documents.** The repository's `docs/proposal.md` and a second draft (`Wayfinder_Hardware_Integrated_Project_Proposal`) both called themselves Version 2.3. They agree on the product: an accessibility-first live-browser companion, with Harbor Line demoted to a regression fixture. They disagree on details:

| Topic | Repository 2.3 | Other draft |
|---|---|---|
| Wire envelope | WIR `0.2`, protocol `0.1`, source `mac_orchestrator`, ids `devkit_tactile`, `esp32_s3_box3`, `p4_eye` | WIR `0.4`, `message_version`, source `wayfinder_mac`, id `box3_surface`, pose `confirmation_required` |
| Page families | FAQ, search, form; "two or three" | Same, plus a real public page "when practical" |
| Observation binding | Evidence references | Observation version bound to every action |
| Release phase | Phases 0 to 7 | Adds Phase 8 release and submission |
| Optional adapters | Model, Sentry, extension | Adds FR-014 model and FR-015 Sentry export as removable |

The LA Hacks sponsor guide that arrived afterward adds constraints. The browser-based core must stay demoable if hardware, credits, or a sponsor SDK is unavailable. Sponsor modules must be removable. Browser Use and Sentry are described as real components only when they are real. A shopping or payment flow must be labeled a simulation. Hardware availability is a risk to plan around, not a given.

## Decision

1. **One proposal.** `docs/proposal.md` Version 2.4 is the only source of truth. It supersedes both 2.3 drafts. Where the other draft is better it is merged: observation versions are bound to actions (a stamp), the public-page path is an honest best-effort extra, and the optional adapters are removable. Where the two disagree on the wire, ADR-002 wins.

2. **The wire does not migrate.** WIR `0.2`, protocol `0.1`, source `mac_orchestrator`, ids `devkit_tactile`, `esp32_s3_box3`, `p4_eye`, and the pose tokens `cleared`, `orient`, `branch_ready`, `needs_confirmation`, `action_verified`, `failure` stay frozen. The other draft's example envelopes (`wayfinder_mac`, `box3_surface`, WIR `0.4`, `confirmation_required`) are not a migration and are not implemented.

3. **The actuator is a 130 mini DC brushless motor.** `contracts.ACTUATOR` is `130_mini_dc_brushless`. The physical meaning of a pose is a *motor run*: `MOTOR_RUNS[token]` gives `duty_percent`, `duration_ms`, and `direction` (`forward`, `reverse`, or `stop`). Pose tokens stay the semantic names. The motor renders them as a short, distinguishable run, not a held position.

4. **`angle_deg` and `pulse_us` remain, labeled a hint.** They stay in the `tactile.render` payload only so that the DevKit firmware source, which was written against an ESC-style 50 Hz pulse, and the tests that read it keep working. They are an unverified pulse hint. They are not a claim that an angle is held, and the UI does not show them as an angle.

5. **No hardware claims.** The UI, README, proposal, handoff, and demo narration must not say a servo is attached. They must not say the motor spun when only the simulator or loopback ran. A physical run is claimed only after a typed DevKit ACK and a human observation, and the first is not yet true. The simulator, loopback, and fixture are always labeled as such.

6. **No invented electrical facts.** Voltage, current, driver model, driver pinout, and the DevKit signal pin remain unverified. GPIO 18 is still only the existing software assumption. The software does not energize anything. The safest wiring invariant is unchanged: do not power the motor from a DevKit pin, and share ground only after the driver is identified. Whether the motor needs a driver board, an ESC, or a different signal is an open bench question, not a software decision.

7. **Sponsor tools are adapters.** Playwright is the only browser adapter implemented. Browser Use, a browser extension, a model, Sentry, Fetch.ai, Creao, Even G2, and QNX are not implemented and are not claimed. The Mac side ships two removable software items that stand in for sponsor-style asks without a sponsor SDK: an NDJSON trace file (`WAYFINDER_TRACE_FILE`) that a collector can read, and a text-only "quiet glance" projection of the same state. Neither is a Sentry or Even Realities integration.

## Consequences

- `/api/health` reports proposal version `2.4` and actuator `130_mini_dc_brushless`.
- `tactile_payload` carries `duty_percent`, `duration_ms`, `direction`, plus the hint fields. The DevKit firmware source still parses `angle_deg`. It has not been compiled or flashed, and it has not been adapted or tested for a brushless motor. New firmware work for the motor is Phase 5 and needs the bench facts above first.
- ADR-001's sentence that the DevKit "drives one servo" is read as "drives one actuator". The actuator is now the motor. The USB star topology is unchanged.
- Proposal sections that said servo are rewritten as motor or actuator. The Braille plate is still a software projection and not a motor command.
- Tests continue to check that the pose tokens and endpoint ids are frozen.

## Not decided here

- Whether the motor is driven by PWM duty on one pin, through an ESC, or through a driver board.
- Supply voltage, stall current, and the safe duty and duration limits. The `MOTOR_RUNS` numbers are renderer defaults for the simulator and the on-screen bar. They are not bench-verified limits.
- Whether a rotary, vibration, or other output replaces the motor later.
