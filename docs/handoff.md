# Agent handoff

Date: 2026-10-18. Written to satisfy proposal 2.4 section 15. The product source of truth is `docs/proposal.md` (Version 2.4, October 18, 2026), which supersedes both documents that were labeled 2.3. This file is an operator handoff. If it disagrees with the proposal on product intent, scope, or acceptance, the proposal wins. If a proposal example disagrees with the running wire format, ADR-002 wins.

## Objective

Keep both suites green: the Harbor Line fixture suite and the browser MVP suite. Do not add Harbor Line features as if they were the product. The software MVP (Phases 2 through 4) is built. The remaining work is hardware (Phase 5 and later) and optional adapters. Neither may be claimed until it actually works.

## Current phase

| Phase | Status |
|---|---|
| Phase 0 Contracts and Mac-owned orchestration | Complete. |
| Phase 1 Harbor Line engineering fixture | Complete locally. |
| Phase 2 Real-browser read-only ingestion | Complete for local pages. Public page is best effort. |
| Phase 3 Bounded live-browser interaction and verification | Complete for local pages. |
| Phase 4 Software MVP across page patterns | Complete for FAQ, search, form, a content page, and the unsupported fallback. |
| Phase 5 Physical DevKit and motor | Not complete. No physical ACK. The motor has not been run by this software. |
| Phase 6 BOX-3 display and touch | Mac adapter only. No firmware. |
| Phase 7 Voice, perception, generalization | Not started. A text-only glance projection exists on the Mac. |

## Files allowed to change

Software continuation may touch `src/wayfinder/`, `apps/`, `tests/`, `firmware/devkit_tactile_adapter/` source and contract checks, and `docs/`. Do not add `firmware/esp32_c3_display`. Do not add BOX or P4X firmware that pretends to be flashed. Do not rename the frozen wire identifiers without an accepted ADR.

## Accepted decisions

- ADR-001. Mac owns canonical WIR. USB star. No S3/C3 pair. No board-to-board application link. `uart` is rejected.
- ADR-002. WIR `0.2`. Device protocol `0.1`. Endpoint ids and pose tokens frozen.
- ADR-003. Harbor Line is the fixture, fallback, and test environment. The software MVP is the bounded live browser.
- ADR-004. The actuator is a 130 mini DC brushless motor. Pose meaning is a motor run. Proposal 2.4 supersedes both 2.3 drafts. The other draft's envelope names are not adopted.
- D-010. Say when an outcome is simulated, loopback, fixture, or physical.
- D-012. Structured tracing is implemented. Sentry is not configured.
- D-014. Playwright is the default browser adapter.

## Schemas and versions

- Proposal version reported by `/api/health`: `2.4`. It also reports `actuator: 130_mini_dc_brushless`.
- WIR `schema_version`: `0.2`
- Device and agent envelope `protocol_version`: `0.1`
- Source: `mac_orchestrator`
- Destinations and sources: `devkit_tactile`, `esp32_s3_box3`, `p4_eye`
- Pose tokens: `cleared`, `orient`, `branch_ready`, `needs_confirmation`, `action_verified`, `failure`
- Motor runs (`MOTOR_RUNS` in `contracts.py`): duty percent, duration ms, direction. `needs_confirmation` is 70% for 140 ms forward. `failure` is 80% for 120 ms reverse. These are simulator and on-screen defaults, not bench-verified limits.
- Pulse hint: `angle_deg` and `pulse_us` still ride in `tactile.render` for unadapted firmware. They are not an angle claim.
- Message types the Mac sends: `health`, `tactile.render`, `safe_disable`, `display.render`, `speech.render`
- Control signal assumption: GPIO 18. Unverified.
- Max JSON body: 1024 bytes. Newline-delimited.

The other 2.3 draft shows WIR `0.4`, source `wayfinder_mac`, id `box3_surface`, `message_version`, and pose `confirmation_required`. None of those is the wire format. Do not migrate without an accepted ADR.

## What the software actually does

`POST /api/sessions` takes an optional JSON body `{"target": "faq" | "search" | "form" | "unsupported" | "public" | "fixture"}`. An empty body opens the Harbor Line fixture. The UI boots on `faq`.

For a browser target, `src/wayfinder/browser/bridge.py` drives headless Chromium through Playwright on a dedicated worker thread (Playwright's sync API cannot change threads, and FastAPI handlers run on a pool). Local pages in `apps/pages` are served by a loopback HTTP server to Chromium. The bridge returns a DOM observation. `families.py` classifies it as FAQ, search, form, content (docs), or unsupported from structure, and plans grounded controls. Each control carries `<stamp>|<recipe>`. The orchestrator re-reads the page before acting and rejects a stale stamp. Submit is consequential and waits for yes. Verification calls `verify_browser(control, fresh_observation)`. The executor receipt is not an argument.

Typed entry: "set room to North room", "type Ada into name", "search for permit". The field must exist on the page and must not be password or card-like.

Trace events cite `browser:<url>#v<read>:<stamp>`. The apply event adds the evidence before, the DOM reference, and the page URL. The check event adds `independent_of_receipt`. Setting `WAYFINDER_TRACE_FILE` appends redacted events as NDJSON.

If Playwright or Chromium is missing, a browser target falls back to the labeled Harbor Line fixture with a reason in the page note. A crashed page becomes an honest failure observation.

The Mac UI at `apps/web` opens on the FAQ page, has buttons for the other targets and the fixture, shows what the adapter read plus a sandboxed snapshot, and keeps the Braille plate, confirmation focus, Escape to decline, 1 to 5 keys, DevKit disconnect, actuator lockout, ping, BOX speech injection, P4 event, and trace id. The tactile panel describes a motor run and says the motor has not spun. A quiet glance panel shows a text-only projection of the state. No glasses are attached.

A duplicate tactile sequence returns `duplicate_sequence` and does not move. Lockout blocks later tactile renders until an explicit enable. `safe_disable` detaches and the next new sequence may reattach. A health ping is not a motor run.

Without `WAYFINDER_DEVKIT_PORT`, the simulator acknowledges and the UI says the motor has not spun. A loopback is status `loopback` and must be described as no physical board.

## Exact acceptance tests

Last local run: 65 passed from the repo venv (`pytest`). CI must report the current count. Do not treat 65 as a permanent number.

| File | Count | Covers |
|---|---|---|
| `tests/test_wayfinder.py` | 25 | Fixture orientation, step-free, confirm and decline, injection, number 9, lockout, protocol rejection, Braille |
| `tests/test_serial_contract.py` | 4 | Loopback contract |
| `tests/accessibility/test_smoke.py` | 2 | Keyboard path, labels, contrast. The first six focusable ids are sensitive. |
| `tests/integration/test_demo_script.py` | 1 | Harbor Line script |
| `tests/test_browser_planner.py` | 12 | Offline planner, stamp, injection, sensitive fields, gateway entry, trace sink, motor payload, glance |
| `tests/browser/test_browser_mvp.py` | 20 | FAQ open, read, follow, back; search; typed entry; form decline and confirm; unsupported; out-of-range; stale choice; stale confirmation; injection; receipt mismatch; trace evidence; public page; BOX parity |
| `tests/integration/test_demo_script_browser.py` | 1 | The browser demo script |

Browser tests need `playwright install chromium`. They skip if Playwright is not importable, and they skip individual cases if Chromium cannot start. The public-page test accepts a real https observation or an honest failure.

## Known verified facts

- ADR-001 topology matches the hardware the user has: one ESP32-S3 DevKitC-1, one ESP32-S3-BOX-3, one P4X-EYE. No second DevKit.
- The actuator is a 130 mini DC brushless motor. The user could not get a servo.
- The Mac UI was exercised in the Cursor browser pane: boot on the FAQ, open a question, number 9, form fill, decline, confirm, search, compare, open a result, back, the unsupported page, the public page, and the labeled fixture. Interaction used DOM `.click()` and the command form because pane pointer clicks did not reach the handlers. No screen reader was used.
- The public page (W3C WAI) was read through the browser in the last run. That depends on the network.
- Firmware source for the DevKit rejects `uart`, the C3 id, a protocol version other than `0.1`, oversize lines, duplicate sequences, and moves inside 50 ms. It has not been compiled or flashed here, and it has not been adapted for a motor.

## Unverified assumptions

- GPIO 18 is only a software assumption.
- The motor's driver or ESC, supply voltage, stall current, signal type, and which DevKit USB socket carries data are unknown.
- Command-to-ACK and command-to-motor latency have not been measured. Do not claim under 250 ms.
- Serial port names in the README are examples.
- A server may be left running at `http://127.0.0.1:8765`. Python changes are not picked up by a running process. Restart it.

## Safety constraints

- Do not power the motor from a DevKit pin. Do not connect an external supply to the DevKit 3.3 V pin.
- Do not energize the actuator until polarity, supply rating, driver input, signal pin, and ground continuity are verified on the exact hardware. The software never energizes anything.
- Stop actuator work on heat, brownout, unstable supply, uncertain polarity, unclear pinout, unexpected motion, or missing common ground. Proposal section 13.6.
- Do not revive `esp32_c3_display`, `esp32_s3_gateway`, board-to-board addressing, or `uart`.
- Do not mark a simulator or loopback result as a physical motor run. Do not say a servo is attached.
- Do not send raw image, audio, or frame fields. Page, camera, and speech text are untrusted.
- Do not make the Harbor Line fixture depend on Playwright or a model. Do not claim Sentry, an LLM, Browser Use, or a physical ACK until they work.

## Evidence produced

- `docs/proposal.md` is proposal 2.4. ADR-003 and ADR-004 record the scope and the motor.
- `src/wayfinder/browser/` and `apps/pages/`: the browser adapter and its pages.
- The test files above.
- DevKit firmware source only: `firmware/devkit_tactile_adapter/`.
- No serial log from a physical board. No latency table. No wiring photos. No motor run.

## Failure result

If the browser adapter fails, the UI falls back to the labeled Harbor Line fixture and says why. If the physical DevKit path fails, label the tactile result as simulated. If a change breaks the browser suite, the fixture suite, protocol rejection, or the Braille projection, the change fails.

## Rollback

The offline fallback is Harbor Line with all boards unplugged and `WAYFINDER_DEVKIT_PORT` unset. Label it as the fixture. Revert a bad contract change by restoring protocol `0.1`, WIR `0.2`, and the endpoint ids in ADR-002. Do not roll back ADR-001 to the retired S3/C3 UART topology. Do not roll back ADR-003 and treat Harbor Line as the product again.

## Next work

1. Hardware, only when the parts are in hand: identify the motor's driver, supply, and signal. Enumerate the DevKit. Get a typed ACK. Adapt the firmware source for the motor. Then run one pose and measure latency. Update `docs/hardware/wiring.md` and `pinouts.md` with verified facts.
2. Optional reaches that stay removable: a model-backed reader, Browser Use or an extension as a second adapter, a Sentry exporter reading the NDJSON file, more page patterns, `choose_option` and `wait_for_change` primitives.
3. BOX display and touch firmware (Phase 6), then voice and perception (Phase 7).
4. Do not add Harbor Line features. Do not migrate the wire format.
