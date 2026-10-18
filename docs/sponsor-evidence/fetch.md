# Fetch.ai — Wayfinder Reader uAgent

Standalone **Reader** specialist from the Wayfinder capability registry (`capabilities.py`), exposed as a discoverable uAgent. The handler is a **tool call** into the existing Mac pipeline (`interpret` → `rank`), not a monolithic prompt. The main demo and orchestrator do **not** import this module.

**Sponsor route (zero-cost):** LA Hacks Fetch.ai / ASI:One **promo codes are expired**; this route is satisfied on **free** Agentverse and ASI:One accounts only—no paid credits assumed.

## Install

```bash
source .venv/bin/activate
pip install -e ".[fetch,dev]"
playwright install chromium   # for faq/search/form/shop/jobs/unlabeled targets (not needed for fixture)
```

## Local run (no Agentverse, no ASI:One)

No `WAYFINDER_ASIONE_KEY`, no Agentverse registration, and no network registration are required to develop or demo the Reader pipeline.

Works offline for `fixture` without Playwright. Other targets need Chromium.

```bash
# Pipeline only (no network, no agent server)
python -m wayfinder.fetch_agent --dry-run fixture
python -m wayfinder.fetch_agent --dry-run faq

# uAgent listening on localhost only (default port 8010)
WAYFINDER_FETCH_REGISTER=0 python -m wayfinder.fetch_agent
```

Environment (optional):

| Variable | Purpose |
|---|---|
| `WAYFINDER_FETCH_PORT` | HTTP endpoint port (default `8010`) |
| `WAYFINDER_FETCH_SEED` | Wallet seed (omit to generate ephemeral) |
| `WAYFINDER_FETCH_REGISTER` | `0` or unset = local only; `1` = register mailbox on Agentverse |
| `WAYFINDER_FETCH_NETWORK` | `testnet` (default) or `mainnet` |

Request shape (uAgents `Model`):

```json
{"target": "faq", "url": null}
```

Targets: `faq`, `search`, `form`, `unlabeled`, `shop`, `jobs`, `fixture`. Optional `url` opens a normalized `http(s)` page through the same Playwright adapter as the Mac demo.

## Agentverse registration (primary evidence — free)

Use a **free** [Agentverse](https://agentverse.ai) account. Registration here is the main sponsor evidence path; it does not require ASI:One credits or hackathon promo codes.

1. Sign in to Agentverse (create a free account if needed).
2. Open **Agents** → **Launch an Agent** (or connect an existing local agent via mailbox, per current Agentverse UI).
3. On your Mac, run the agent with mailbox registration enabled (keep seeds and any Agentverse API tokens in the shell only—never commit them):

   ```bash
   export WAYFINDER_FETCH_REGISTER=1
   export WAYFINDER_FETCH_SEED="your recovery phrase or generated seed"
   # If Agentverse docs require an API key for mailbox connect, set it here:
   # export <AGENTVERSE_API_KEY_ENV>=...
   python -m wayfinder.fetch_agent
   ```

4. In Agentverse, confirm the agent profile lists **Wayfinder Reader**, read-only trust, and protocol `WayfinderReader@0.1.0`.
5. From another uAgent client (or Agentverse messaging, if offered), send a `ReaderRequest` and verify the reply includes `summary`, `branch_labels`, and `capability_name: "Reader"`.
6. Copy the public **agent profile URL** into the evidence section below.

## Optional — ASI:One (free key, non-blocking)

ASI:One is **optional** for this route. A missing `WAYFINDER_ASIONE_KEY` is normal; the uAgent starts and serves `WayfinderReader@0.1.0` without it.

If you want a **Share-chat** link for judges:

1. Create a **free** ASI:One account at [ASI:One](https://asi1.ai) (no promo code; do not assume paid balance).
2. Generate an API key in ASI:One settings and export it locally only:

   ```bash
   export WAYFINDER_ASIONE_KEY="your free-tier key"
   ```

   The Reader uAgent does **not** require this key to run; it is only for using ASI:One’s web chat UI against your registered agent, if you choose to demo that way.

3. In ASI:One, start a chat that reaches your Agentverse-registered agent and send a `ReaderRequest`-equivalent message (e.g. ask to read target `fixture` or `faq`).
4. Use ASI:One’s **Share** action and paste the Share-chat URL into the evidence section below.

## Local evidence (no ASI:One key, no Agentverse login)

Captured in-repo on **2026-10-18** with:

```bash
unset WAYFINDER_ASIONE_KEY
.venv/bin/python -m wayfinder.fetch_agent --dry-run faq
```

**Summary:** Library card help. Topics: When is the library open?, How do I get a card?, How do I renew a book?.

**Branch labels:**

- Explain this page
- List the questions
- Open When is the library open?
- Open How do I get a card?
- Open Billing questions

(`capability_name`: Reader, `trust`: read_only, `pipeline_tool`: interpret+rank, `error`: null)

## Evidence URLs (paste after registration)

**Agentverse profile URL (TODO):**

```
https://agentverse.ai/agents/details/agent1qddkgjy5h05zh5px74xcmnc9ehmf3s4z39x8jdj87yg46caul7s4v3w939p/profile
```

**Agent inspect URL (from uAgents startup with `WAYFINDER_FETCH_REGISTER=1`, ephemeral wallet — 2026-10-18):**

Almanac API registration succeeded (`status: active`, `type: local`); on-chain Almanac contract registration was still funding via testnet faucet when captured. Endpoint config overrides mailbox until the agent is linked in Agentverse while signed in.

```
https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A8010&address=agent1q0eecunc4dg9zg39pqx69mqhk98dxve6a2hpsruznn58e69xvxxj6l4c3jf
```

**ASI:One Share-chat URL (TODO — optional):**

```
<!-- TODO: paste ASI:One share-chat URL showing a ReaderRequest interaction here -->
```

## What still requires a human

- Free Agentverse account → register **Wayfinder Reader** → paste **Agentverse profile URL** above.
- Optionally: free ASI:One API key → one Share-chat demo → paste **ASI:One Share-chat URL** above.
- Keep wallet seeds, Agentverse tokens, and ASI:One keys in environment variables only.
