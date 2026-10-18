# Sentry — Wayfinder observability bridge

Removable adapter on the Mac orchestrator. The Harbor Line demo, tests, and default boot path do **not** require a DSN or `sentry-sdk`. Structured turn events stay in-process; this layer mirrors them into Sentry when you opt in.

## Capabilities enabled (sponsor bar)

When `WAYFINDER_SENTRY_DSN` is set and `pip install -e ".[sentry]"` is installed:

1. **Error monitoring** — model backend failures (`ModelCallError`, timeouts, bad JSON) are reported with `capture_exception` while the turn still falls back to the deterministic interpreter and ranker.
2. **Tracing** — each orchestrator turn opens a transaction `wayfinder.turn` tagged with `trace_id`, `session_id`, and `turn_id`. Child spans mirror stage events for `interpreter`, `ranker`, `router`, `policy`, `executor`, and `verifier`. Optional model work appears as `wayfinder.model` when `WAYFINDER_MODEL` is not `off`.
3. **Profiling** — `profiles_sample_rate=1.0` is set at init so performance profiles can attach to those transactions in the Sentry UI (environment `hackathon`).

## Install and enable

```bash
source .venv/bin/activate
pip install -e ".[sentry,dev]"
export WAYFINDER_SENTRY_DSN="your project DSN from sentry.io"
python -m wayfinder.api
```

Leave `WAYFINDER_SENTRY_DSN` unset for normal development and CI.

## Reproduce one captured error (model path)

1. Keep `WAYFINDER_SENTRY_DSN` set and start the API as above.
2. Point the model at a backend that will fail, without changing core behavior for everyone else:

   ```bash
   export WAYFINDER_MODEL=asione
   unset WAYFINDER_ASIONE_KEY
   ```

3. Open a session and trigger a turn that runs the model path (browser or fixture with model enabled), e.g. `what is here` after setting `WAYFINDER_MODEL=ollama` with nothing listening on port 11434, or ASI:One with a missing key as above.
4. In [Sentry → Issues](https://sentry.io), open the new issue (e.g. `ModelCallError: missing_key` or a connection error). The user-visible speech and verification outcome should match an run without Sentry — only telemetry is added.

## Reproduce a verification failure (no exception required)

1. Create a session on the Harbor fixture.
2. Enable the injected fault: `POST /api/sessions/{id}/faults` with `{"name": "verification_mismatch", "enabled": true}`.
3. Run an action that applies but fails verification (e.g. step-free filter). The turn transaction still shows executor and verifier spans with `FAILURE` status tags.

## Debugging story template (fill after a real trace)

While investigating turn **`trace_id=c2e5a7a15bd541c99ff953749a6bef23`** in Sentry Performance, we opened transaction **`wayfinder.turn`** (environment `hackathon`). The waterfall showed router, policy, executor, verifier, and an interpreter reread. The slowest span on that screen was **`wayfinder.turn`** at **`0.91 ms`**. The user heard **Verification: SUCCESS** after confirming the step-free arrival alert. Interpreter, ranker, router, and policy were on the earlier “what is here” turn **`trace_id=2cdd265a5b2c410b9e278d2f4d72341c`**, whose root duration on screen was **0.68 ms**.

## Capture the waterfall (human steps)

1. Set `WAYFINDER_SENTRY_DSN` and run one turn from the web UI or `POST /api/sessions/{id}/turns`.
2. Copy `trace_id` from the JSON response or `GET /api/sessions/{id}/traces`.
3. In Sentry, go to **Performance** → search **`trace_id:<paste>`** or filter environment **`hackathon`** and open the matching **`wayfinder.turn`** transaction.
4. Expand child spans (`wayfinder.interpreter`, `wayfinder.ranker`, …, and `wayfinder.model` if the model was on) to view the waterfall and any linked error event.
