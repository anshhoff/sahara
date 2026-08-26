# Failed Subscription Recovery Agent
*Razorpay AI Buildathon — Track 03: AI Revenue Recovery*

## The problem

A recurring subscription charge fails. Razorpay retries on its own schedule, the
subscription halts, and the merchant discovers the churn weeks later. The recoverable
window — the first hours and days after failure — is exactly when nothing happens.

## What it does

Detects a failed Razorpay Subscription charge via webhook, diagnoses why it failed
(6 fixed categories), picks ONE intervention from a deterministic policy table
(retry later / update-payment link / promise-to-pay / stop-and-handoff), executes it
against Razorpay test-mode APIs, and stops when hard rules say stop. Every step is
audited; every reported rupee traces to case IDs.

```
webhook ─▶ DETECT ─▶ DIAGNOSE ─▶ [STOP GATE] ─▶ DECIDE ─▶ [STOP GATE] ─▶ EXECUTE ─▶ OUTCOME
           verify     rules R1–R7   I1–I4        static     I1–I4         test-mode   recovery
           dedupe     else model    pre-decision  policy    pre-execution  API +       signal or
           route      → 6-value     gate          table     gate           simulated   next failure
                        enum                                              notification
```

## Results (89 cases: 88 synthetic [seed 42] + 1 live test-mode)

| Metric | Value |
|---|---|
| ₹ at risk | ₹72,911 across 89 cases (₹72,412 synthetic + ₹499 live) |
| ₹ recovered | ₹30,550 (56.8% of the 88 synthetic cases; 56.2% of all 89, the live case still being open) |
| Avg time to recovery | 46.0 h (simulated clock for synthetic cases) |
| Recovered | 50 |
| stopped_opt_out | 2 |
| stopped_unknown | 8 |
| stopped_max_attempts | 0 — see note below |
| stopped_cooldown_expired | 0 |
| stopped_handoff (policy stop / lapsed promise) | 28 |
| Still open | 1 — the live case, awaiting its 24 h retry |
| Classification accuracy vs ground truth | 100% (84/84) rules path · 25% (1/4) model path, with **no model configured** |

Reproduce, with no accounts, no API keys and no model download:

```bash
python scripts/generate_synthetic.py --n 80 --seed 42 --out synthetic_cases.json
LLM_PROVIDER=none python scripts/run_batch.py --cases synthetic_cases.json --db recovery.db
```

That reproduces the 88 synthetic rows exactly. It does **not** reproduce the 89th — the
live case needs a Razorpay test key and the webhook secret (see "The one live case").
`/api/summary` always reports the split as `n_synthetic` / `n_live`, and gives both a
synthetic-denominator `rate` and an all-cases `strict_rate`, so the two are never
silently blended.

Trace any number: `GET /api/metrics/trace/recovered` returns the exact case IDs behind
it, and each of those IDs drills down to an `outcome` entry in its audit trail.

Reading the table honestly:

- **The 4 model-path cases are cases the rules could not reach.** The frozen run above
  used `LLM_PROVIDER=none`, so all four collapsed to `unknown` and stopped. One of them
  genuinely *was* unknown, hence 1/4. With a model configured, those are the cases it
  earns its place on; without one, they stop instead of being guessed. Both behaviours
  are correct, which is the whole argument.
- **`stopped_max_attempts` is 0 because the policy table makes attempt 3 terminal in
  every row.** Invariant I1 is a backstop against a wrong policy table, not a path the
  table takes; `tests/test_invariants.py` drives a case to the cap directly and asserts
  I1 fires. `SYNTH-E-03` is the batch's version of the same story: exactly 3 attempts,
  then a stop, and a fourth attempt is impossible.
- **The 28 handed-off cases are a deliverable, not a failure.** Each one has a complete
  case file at `GET /api/cases/{id}` — that response *is* the handoff artifact.
- **Outcome probabilities are modelling assumptions, not measured industry data**
  (`scripts/run_batch.py`, `SUCCESS_PROBABILITY`). The measured thing here is the
  *mechanism*: synthetic cases traverse the identical code path as live webhooks,
  entering only through `intake()` and advancing only through `tick()`.

## The one live case

Case `case_01M0STYHMCZK8CNBR5E2DBJ000` is real: a `payment.failed` body signed with the
real `RAZORPAY_WEBHOOK_SECRET` (HMAC-SHA256 over the raw body, Razorpay's own scheme),
POSTed to a running receiver on a live `rzp_test_*` account. It verified, deduped,
diagnosed and decided:

```
detect ─▶ diagnose  method="rule"  matched_rule="R2"  confidence=1.0  llm_model=null
       ─▶ [I1–I4 gate] ─▶ decide  RETRY_LATER  delay 24 h  attempt 1
```

`llm_model=null` on a real delivery is the boundary claim holding in the wild, not just
in tests: an unambiguous failure never reaches the model.

Four probes against the same endpoint, all behaving as specified:

| Probe | Result | HTTP |
|---|---|---|
| Valid signature, first delivery | `processed`, one case created | 200 |
| Byte-identical replay | `duplicate`, no second case | 200 |
| One byte flipped (`49900`→`99900`), original signature | `rejected: invalid signature` | 400 |
| No `X-Razorpay-Signature` header | `rejected: invalid signature` | 400 |

A real test-mode Payment Link was also created through `executor._create_payment_link()`
— `plink_TTbOzWvlCm2ufM`, `notify {email,sms,whatsapp} = false`, `reminder_enable=false`.
That is the executor's actual money-moving call, against Razorpay, contacting nobody.

**What is not proven, stated plainly:** the delivery was signed and sent locally rather
than emitted by Razorpay's dispatcher. Subscriptions is not provisioned on the test
account — `/v1/plans` and `/v1/subscriptions` return `401 Unauthorized` while `payments`,
`orders`, `customers`, `items` and `payment_links` all return `200`, reproduced through
curl, through Razorpay's own CLI, and through the dashboard UI (which shows "Something
went wrong" on both read and write). With no plan there is no subscription, hence no
"Charge this now", hence no Razorpay-originated webhook. Everything from signature
verification inward is the production path; the unproven span is the tunnel, not the
logic. Full record: `docs/notes-test-cards.md`.

## Where the LLM is — and is not

The LLM does exactly two things: (1) classifies ambiguous failure reasons into a fixed
6-value enum, validated client-side by a Pydantic schema — anything uncertain,
malformed, or unavailable collapses to `unknown`, and `unknown` always stops;
(2) drafts notification copy that a deterministic validator checks (amounts, links,
disclosures) with a static-template fallback. It never chooses interventions, never
sets retry timing, never touches stopping rules, and has no API credentials or tool
access. Retry timing, the policy table, the four stop invariants, and all money-moving
calls are deterministic code with hardcoded bounds.

| Capability | Owner |
|---|---|
| Failure classification, clear cases | Rule table R1–R7 on Razorpay error fields |
| Failure classification, ambiguous cases | **LLM**, output forced into the enum; `< 0.8` confidence → `unknown` |
| Intervention choice | Static `(category, attempt) → action` table |
| Retry timing | Hardcoded delays per policy row |
| Stopping rules I1–I4 | Independent module, run before every decision *and* every execution |
| Message copy | **LLM** drafts; deterministic validator; static template on rejection |
| Money-moving execution | Deterministic executor calling Razorpay test-mode APIs |

The bounds, in one line: **at most 3 attempts per case, at least 24 h between customer
contacts, opt-out stops everything, an unknown cause is never acted on.**

Checkable claims, not assertions:

```bash
grep -rlE '^\s*(from|import)\s+openai' app/     # -> app/llm.py, and nothing else
grep -rn 'UPDATE audit_log|DELETE FROM audit_log' app/   # -> no matches, ever
pytest tests/test_llm_boundary.py               # asserts both of the above
```

(`app/config.py` contains the *string* `openai_compat` as a provider name, so a bare
`grep -rl openai app/` also matches that file. The import grep above is the real check,
and `tests/test_llm_boundary.py::test_only_llm_py_imports_the_model_client` enforces it.)

Worst-case hallucination = a wrong-but-valid enum value, still capped at 3 bounded
attempts and fully audited — or an invalid output, which stops the case. Proof: the
frozen batch above ran with `LLM_PROVIDER=none` and every case still completed,
audited and bounded.

## Architecture

One FastAPI process serves the webhook receiver, the JSON API and the static
dashboard. One SQLite file holds six tables. There is no job queue: `RETRY_LATER`
decisions write a `scheduled_for` timestamp, and a `tick()` scan executes what is due —
every 30 s in live mode, and directly against a **simulated clock** in batch mode, so a
14-day recovery episode resolves in milliseconds through the same code path.

```
app/
  main.py        FastAPI app, webhook route, startup, background tick loop
  config.py      bounds, category/action enums, rule table, policy table, copy templates
  db.py          sqlite helpers, schema application, id generation
  clock.py       injectable clock (real vs simulated) — nothing calls datetime.now()
  cases.py       RecoveryCase aggregate + the status state machine
  webhooks.py    signature verify, dedupe, intake, case routing          (Detect)
  diagnosis.py   rule table, then the model                             (Diagnose)
  llm.py         the ONLY module importing the model client             (2 leaf calls)
  policy.py      static table lookup                                    (Decide)
  invariants.py  I1–I4, pre-decision and pre-execution gates            (Stop)
  executor.py    Razorpay calls, simulated notifications, copy validate, tick()
  audit.py       append-only audit writer
  metrics.py     every metric as (value, case_ids)
  api.py         dashboard JSON endpoints
schema.sql       six tables, with CHECK constraints as the enum backstop
scripts/         generate_synthetic.py, run_batch.py, check_llm.py
dashboard/       static HTML + vanilla JS + one CSS file, no build step
tests/           80 tests: rules, policy matrix, invariants, copy validator, boundary, API
```

`cases.py` is a ninth module beyond the eight named in `docs/01` §3; it exists only to
hold `transition()` without creating an import cycle between `db.py` and `audit.py`.

## Setup

Python 3.11+ is the documented target (`docs/10` §1). This build was written and
verified on Python 3.9.6; nothing in it needs a newer runtime.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # or: pip install fastapi "uvicorn[standard]" \
                                         #     pydantic razorpay openai python-dotenv pytest httpx
cp .env.example .env                     # edit only if you want the live or LLM paths
```

### Run the batch and the dashboard (no accounts needed)

```bash
python scripts/generate_synthetic.py --n 80 --seed 42 --out synthetic_cases.json
LLM_PROVIDER=none python scripts/run_batch.py --cases synthetic_cases.json --db recovery.db
LLM_PROVIDER=none uvicorn app.main:app --port 8000
# open http://localhost:8000/
pytest -q
```

### Environment variables

| Variable | Required for | Where it comes from |
|---|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real test-mode API calls (Payment Links) | Dashboard → API Keys, test mode (`docs/02` §2) |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook signature verification | You choose it when creating the webhook (`docs/02` §6) |
| `LLM_PROVIDER` | `openai_compat` (hosted free tier) / `ollama` (local) / `none` | `docs/10` §5 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Whichever provider you picked | `docs/10` §5.2–5.3 |
| `DB_PATH` | Pointing at a scratch database | optional, defaults to `recovery.db` |
| `LLM_COPY_ENABLED` | Kill-switch forcing static templates | optional, defaults to `true` |
| `LIVE_LINKS_MAX` | Cap on real test-mode Payment Links | optional, defaults to `5` |

Never put live-mode keys anywhere in this project. `.env` is gitignored;
`.env.example` holds placeholders only.

### The three LLM modes

All three are free and all three are reached through the same `openai` client — only
`LLM_BASE_URL` and `LLM_MODEL` change, because `app/llm.py` is the only module that
knows a model exists.

| `LLM_PROVIDER` | What runs | Needs | Cost |
|---|---|---|---|
| `openai_compat` | A hosted OpenAI-compatible free tier (Groq / OpenRouter / Google AI Studio) — **nothing runs on your machine** | free signup + key | ₹0 within free-tier limits |
| `ollama` | A local open-weights model (`ollama serve && ollama pull qwen2.5:7b-instruct`) | ~5 GB disk, 8 GB+ RAM | ₹0, offline, no rate limit |
| `none` | No model at all: ambiguous cases become `unknown` and stop, copy uses static templates | nothing | ₹0, no setup |

**On a laptop that cannot spare the RAM for a local model, use `openai_compat`** — that
is exactly what `docs/10` §5.3 is for. Copy `.env.example` to `.env`, uncomment your
provider, and paste a **current** model id from that provider's own model page: model
ids and free-tier limits change often, so do not take one from any documentation,
including these docs.

Then verify it before you trust a batch:

```bash
python scripts/check_llm.py
```

This matters because of how the boundary is built: if the provider is unreachable,
every classification collapses to `unknown`, every ambiguous case stops, and the batch
still exits 0 with all acceptance checks green. That is the correct behaviour — but it
is indistinguishable from a forgotten API key. `check_llm.py` tells the two apart, and
both `uvicorn` startup and `run_batch.py` warn if a hosted provider is half-configured
(e.g. `openai_compat` with `LLM_BASE_URL` still pointing at localhost).

There is no paid API anywhere in this project. `none` is not a degraded mode to hide —
it is the proof that the guardrails do not depend on model quality.

**Coding agents are not providers.** opencode, Claude Code, Cursor and Aider are
terminal/IDE agents that help you *write* this project; they are not inference
endpoints the app can call, they each still need a model provider behind them, and they
must never appear in `requirements.txt` (`docs/10` §5.4, §9).

### Live mode (Razorpay test mode + ngrok)

Follow `docs/02-razorpay-setup.md` end to end: test keys, plan `demo-monthly-499`, one
authenticated subscription, a webhook pointed at
`https://<your-ngrok>.ngrok-free.app/webhooks/razorpay` subscribed to `payment.failed`,
`subscription.pending`, `subscription.halted`, `subscription.charged` and
`payment_link.paid`. Then force a charge failure and watch it traverse the same loop
the batch runs. Real Payment Links are created with SMS and email notifications
explicitly disabled — Razorpay never contacts anyone in this project.

## Verifying the numbers

The README, the dashboard and raw SQL must agree to the paisa (`docs/07` §5):

```bash
curl -s localhost:8000/api/summary | python -m json.tool
sqlite3 recovery.db "SELECT status, COUNT(*), SUM(amount_at_risk_paise)
                     FROM recovery_case GROUP BY status;"
curl -s localhost:8000/api/metrics/trace/recovered
```

Two consecutive clean-room runs on the same seed produce identical numbers; batch
ordering is by insertion (`rowid`), never by a random id tail.

## Honest limits & out of scope (deliberate)

- **No webhook has arrived from Razorpay's own dispatcher.** A real test-mode account is
  wired in, a real Payment Link was created through it, and a correctly-signed
  `payment.failed` was verified, deduped and processed end to end — but that delivery was
  signed locally, because Subscriptions is not provisioned on the account (`/v1/plans` →
  `401`, reproduced three ways). No plan, so no subscription, so no "Charge this now".
  What is unverified is the tunnel, not the logic. See "The one live case".
- **No test card has been exercised.** `docs/notes-test-cards.md` records why: the card
  matrix is reachable only through a subscription authentication flow, which the same
  `401` blocks. Category breadth comes from the synthetic set, as designed.
- **A real model has now been called.** `scripts/check_llm.py` reached a hosted
  OpenAI-compatible provider and classified 4/4 rule-proof probes correctly. The frozen
  88-case batch above was still run with `LLM_PROVIDER=none`, so its `1/4` model-path
  figure stands as reported; the mocked-client tests (invented category, low confidence,
  prose instead of JSON, code-fenced JSON, provider timeout) remain the boundary's
  guarantee.
- `retry_charge` is always recorded as `simulated`: test mode exposes no stable API for
  forcing a subscription charge retry, and inventing a "live" record would be a lie in
  an audit-trail project.
- Checkout abandonment, B2B receivables, real SMS/email delivery and learned retry
  timing are out of scope by choice — see `docs/00-overview.md` §2.2 for why, and what
  reuse would look like. Each is a new detector and policy table over the same
  Diagnose→Decide→Execute→Stop skeleton.
- Every simulated message carries the literal `[SYNTHETIC DEMO]` disclosure, and the
  copy validator rejects any draft without it. Nothing is ever transmitted anywhere.

## Documentation

`docs/00-overview.md` through `docs/10-tech-stack.md` are the specification this
implementation follows. `docs/11-setup-and-usage.md` is the operator's guide — install,
configure, run, use the dashboard and API, and troubleshoot, in more detail than the
Setup section above. `docs/future-work-notes.md` records everything wished for after the
freeze.

## License

MIT — see `LICENSE`.
