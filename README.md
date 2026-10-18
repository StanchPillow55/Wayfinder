# Wayfinder

Wayfinder is an accessibility-first live-browser companion. It reads the page a person is on, turns it into a short semantic interface, offers three to five grounded actions, confirms before anything consequential, checks the result by reading the page again, and carries the same state through speech, a text glance, and a tactile output. It sits beside the work and sees the current context, in the spirit of tools like HeyClicky, but it is built around modality independence, confirmation, and independent verification.

The Mac is the orchestrator. [ADR-001](docs/adr/0001-mac-orchestrated-usb-topology.md) records the USB star topology. [ADR-003](docs/adr/0003-bounded-live-browser-mvp.md) records the product scope. [ADR-004](docs/adr/0004-motor-actuator-and-proposal-reconciliation.md) records the motor actuator and the reconciliation of the two 2.3 proposal drafts. The source of truth is [docs/proposal.md](docs/proposal.md), Version 2.4.

```
Mac  — canonical state, Playwright browser adapter
 ├── browser  — headless Chromium — FAQ, search, form pages (and an optional public page)
 ├── USB serial — ESP32-S3 DevKitC-1 — motor driver — 130 mini DC brushless motor
 ├── USB serial — ESP32-S3-BOX-3 — display, touch, voice
 └── USB debug  — P4X-EYE — camera events
```

## What runs today

The Mac UI opens on a real browser page. Playwright drives headless Chromium against realistic pages in `apps/pages`:

- **FAQ** (`library-help.html`, `billing-questions.html`): list questions, open one and read its answer, follow a link, go back.
- **Search** (`city-services.html`): search, filter, clear, compare two results, open a result, go back.
- **Form** (`room-request.html`): inspect, fill, and submit. Submit waits for a yes. A no leaves the page unchanged.
- **Unsupported** (`market-poster.html`): Wayfinder says it cannot offer actions and invents none.
- **Public page** (best effort): reads a public page if the network allows, otherwise says it could not.
- **Harbor Line**: a labeled fixture. It is the regression suite and the offline fallback. It is not the demo story.

Page text is untrusted. The FAQ and form pages contain "Ignore previous instructions ..." sentences. They are shown as untrusted text and cannot submit anything or skip confirmation.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
pytest
uvicorn wayfinder.api:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765 and follow [docs/demo/script.md](docs/demo/script.md). `?target=search`, `form`, `unsupported`, `public`, or `fixture` opens another page. If Playwright or Chromium is missing, Wayfinder says so and shows the labeled Harbor Line fixture.

`POST /api/sessions` with `{"target": "faq" | "search" | "form" | "unsupported" | "public" | "fixture"}` opens a session on that page. An empty body opens the Harbor Line fixture.

Optional environment variables:

| Variable | Effect |
| --- | --- |
| `WAYFINDER_DEVKIT_PORT`, `WAYFINDER_BOX3_PORT`, `WAYFINDER_P4_PORT` | Use a real serial port instead of the simulator. Only after the board speaks the protocol. Port names are examples. |
| `WAYFINDER_PUBLIC_URL` | Override the public page. |
| `WAYFINDER_TRACE_FILE` | Append each redacted trace event as a JSON line. This is a file, not Sentry. |

`pyserial` is optional (`pip install -e ".[serial]"`).

## What is real, simulated, or unverified

**Real and tested** (65 tests in the last run): the Playwright adapter; FAQ, search, and form flows with multi-page navigation; verification from a fresh page read, not the executor receipt; confirm and decline; stale evidence rejection; out-of-range rejection; the unsupported fallback; the public page read or honest failure; the prompt-injection paths; one trace id per turn that cites browser evidence; the Mac UI; the fixture regression suite.

**Simulated**: every board. DevKit acknowledgements, BOX-3 display and speech, and P4X-EYE events come from the simulator unless a port is set. The UI says so. The tactile view is the planned motor run drawn on screen, and it says the motor has not spun.

**Not done**:

- No physical board has acknowledged anything. The motor has not been run by this software.
- The actuator is a 130 mini DC brushless motor, not a servo. It spins and does not hold an angle. Pose tokens are unchanged. Their physical meaning is a motor run (`duty_percent`, `duration_ms`, `direction`). `angle_deg` and `pulse_us` remain only as an unverified pulse hint for firmware that has not been adapted.
- The DevKit firmware source has not been compiled, flashed, or adapted for the motor. BOX-3 and P4X-EYE firmware do not exist.
- No LLM, Browser Use, browser extension, Sentry, Fetch.ai agent, or glasses SDK. Playwright is the only adapter. The text "quiet glance" is a software projection with no glasses attached.
- Unsupported: logins, CAPTCHAs, select menus, checkboxes, iframes, shadow DOM.

**Unverified on the bench**: GPIO 18, the motor's driver or ESC, supply voltage and current, the DevKit USB socket, and command-to-motor latency. No voltage, current, or pinout is claimed. See [docs/hardware/wiring.md](docs/hardware/wiring.md).

## Proposal map

| Proposal piece | Where it lives |
| --- | --- |
| WIR and device messages | `src/wayfinder/models.py` |
| Browser adapter and page families | `src/wayfinder/browser/` |
| Orchestrator, policy, verifier | `src/wayfinder/orchestrator.py` and the modules it calls |
| Local pages served to the adapter | `apps/pages` |
| Web UI | `apps/web` |
| API | `src/wayfinder/api.py` |
| Harbor Line fixture | `src/wayfinder/demo_site.py`, `src/wayfinder/executor.py` |
| DevKit firmware (source only) | `firmware/devkit_tactile_adapter` |
| Hardware decision | `docs/adr/0001-mac-orchestrated-usb-topology.md` |
| Contract versions | `docs/adr/0002-freeze-wir-0-2-and-protocol-0-1.md` |
| Product scope | `docs/adr/0003-bounded-live-browser-mvp.md` |
| Motor and reconciliation | `docs/adr/0004-motor-actuator-and-proposal-reconciliation.md` |
| Proposal 2.4 | `docs/proposal.md` |
| Demo script | `docs/demo/script.md` |
