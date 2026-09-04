# VERIFY — every claim, the artifact that proves it, and the command that re-proves it

Nothing on this page asks to be believed. Each row names a number, the file it comes
from, and a command you can run to derive it yourself from the seed.

**One command checks the whole page:**

```bash
python scripts/verify_numbers.py --check
```

It replays the batch in a throwaway database, extracts all 118 published values, and
byte-compares them against `docs/verified-numbers.json`. **Editing a digit in the
README's results table makes CI exit non-zero.** It re-derives rather than reading
`recovery.db`, because a checked-in database can be edited and the point is to check
the committed numbers against the code that is in the tree right now.

Numbers changing is not the failure this guards against — every headline here has moved
twice already, both times because a measurement was fixed. What it guards against is
them changing **quietly**: `--update` is a commit somebody has to make and explain, and
CI is red until they do.

---

## Reproducing the batch from nothing

No accounts, no API keys, no model download:

```bash
pip install -r requirements.txt
python scripts/generate_synthetic.py --n 80 --seed 42 --out synthetic_cases.json
LLM_PROVIDER=none python scripts/run_batch.py \
    --cases synthetic_cases.json --db recovery.db --seed 42 --holdout 0.35
```

That run exits non-zero if any of its acceptance checks fail, so the command **is** the
assertion. Everything below comes out of it.

---

## The headline claims

**Batch:** n = 88 (80 sampled + 8 edge cases), seed 42, holdout 0.35, `LLM_PROVIDER=none`.

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| **Net incremental recovery** | **₹12,921**, 95% CI [₹5,640, ₹20,822] — excludes zero | `docs/verified-numbers.json` → `net.net_incremental_paise` | `curl -s :8000/api/summary \| jq .net` |
| **Lift, treated vs control** | **+49.8 pp**, 95% CI [+31.0, +66.9] — excludes zero | `…json` → `incremental.lift` | `curl -s :8000/api/summary \| jq .incremental` |
| Recovery rate by arm | 36/52 = 69.2% treated · 7/36 = 19.4% control | `…json` → `incremental.treated/control` | as above |
| Gross recovered | ₹21,157 of ₹43,411 at risk | `…json` → `net.gross_recovered_paise` | `curl -s :8000/api/metrics/trace/recovered` |
| Cost | ₹1,409 = ₹729 outreach + ₹680 human queue | `…json` → `costs` | `curl -s :8000/api/summary \| jq .costs` |
| Cost per ₹100 recovered | ₹6.66 | `…json` → `net.cost_per_100_recovered` | as above |

## What the agent refused to do

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| Cases deliberately not contacted | **9** (+ 27 held out to measure the rest) | `…json` → `declined_to_contact` | `curl -s :8000/api/declined` |
| Outreach to already-settled customers | **0 of 51 dispatches fenced** | `…json` → `fencing` | `curl -s :8000/api/fencing` |
| Compensation entries | 0 — no dispatch turned out wrong after the fact | `…json` → `fencing.n_compensations` | as above |
| Real phone calls placed, ever | **0** — `plivo_trial_verified` is 0 in every run | `execution_record.mode` | `sqlite3 recovery.db "SELECT mode, COUNT(*) FROM execution_record GROUP BY mode"` |

## The escalation ladder

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| Executions by action | 30 retry · 38 link · 4 promise · **9 voice** | `…json` → `executions_by_action` | `curl -s :8000/api/summary \| jq .executions_by_action` |
| Dated promises the customer named | 4 — 2 kept, 2 broken | `…json` → `promises` | `curl -s :8000/api/promises` |
| **What the voice rung is worth** | **+7.4 pp**, 95% CI [+2.2, +12.5] — excludes zero | `docs/voice-rung.json` | see below |
| The same rung, in rupees | ₹+49,519, 95% CI [−₹66,790, +₹168,596] — **includes zero** | `docs/voice-rung.json` → `delta` | see below |

```bash
python scripts/generate_synthetic.py --n 2000 --seed 42 --out synthetic_cases_2000.json
LLM_PROVIDER=none python scripts/run_batch.py --cases synthetic_cases_2000.json \
    --db on.db  --seed 42 --holdout 0.35
LLM_PROVIDER=none python scripts/run_batch.py --cases synthetic_cases_2000.json \
    --db off.db --seed 42 --holdout 0.35 --no-voice
python scripts/measure_rung.py --with on.db --without off.db
```

> The rate lift is a result. **The rupee figure is not one yet** — its interval spans
> zero, and `docs/analysis-plan.md` forbids quoting the first while the second does.

## The model, measured rather than asserted

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| Model vs rules, **policy facts** | 90.0% [74, 97] vs 60.0% [42, 75], McNemar p = **0.0117** | `docs/ablation-inbound.json` | `python scripts/ablate_inbound.py` |
| Model vs rules, intent | 96.7% [83, 99] vs 60.0% [42, 75], p = 0.0010 | same | same |
| Model vs rules, date | 93.3% [79, 98] vs 83.3% [66, 93], p = 0.3750 — **not significant** | same | same |
| Classification accuracy, rules path | 100% (84/84) | `…json` → `classification.rule` | in the batch summary |
| Classification accuracy, model path | 25% (1/4) — **with no model configured**, so 3 of 4 correctly stopped as `unknown` | `…json` → `classification.llm` | same |

The ablation needs a provider. Everything else on this page runs offline.

## Calibration — the priors, scored

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| Brier score | 0.2581 (0.25 is what always answering 0.5 scores) | `…json` → `calibration` | `curl -s :8000/api/calibration` |
| ECE | 0.1064 | same | same |
| Executions scored | 81 of 81 | same | same |
| Where the agent is over-confident | `card_expired / VOICE_CALL`: prior 0.34, realised 0.20 | `/api/calibration` → `by_pair` | same |

## Integrity

| Claim | Value | Artifact | Re-prove it |
|---|---|---|---|
| Audit chain | intact, 545 entries, 0 unchained | `…json` → `audit_chain` | `curl -s :8000/api/audit/verify` |
| Tamper detection actually works | editing one row breaks it | — | `sqlite3 recovery.db "UPDATE audit_log SET summary='x' WHERE id=5"` then re-run the command above |
| Every acceptance check | 20 of 20 pass | batch exit code | the reproduce command above |
| Test suite | 225 passing | `pytest` exit code | `python -m pytest -q` |

## Where a number is deliberately absent

A missing measurement is never rendered as a zero, and these are the places it would be
tempting to:

| Number | Why it is absent |
|---|---|
| Per-category lift for a cause with an empty control arm | Reported as **not measurable**. Having nothing to say and having measured no effect are different claims. |
| `organic` recovery on a live database | The provenance stamp only exists on simulated recovery events. Reports as unavailable, not 0. |
| A fence verdict that could not reach the truth | Recorded as `unverified`, which does not block and is counted separately — "we did not check" must never look like "we checked and it was fine". |
| A real phone call | There has not been one. The path exists and is gated by I8; the mode `plivo_trial_verified` reads 0 because it is 0. |

## The assumptions, stated as assumptions

None of these is measured, and no number above is stronger than the weakest of them:

| Assumption | Where | What it decides |
|---|---|---|
| `SUCCESS_PROBABILITY` | `scripts/run_batch.py` | whether a simulated intervention works |
| `BASELINE_RECOVERY_PROBABILITY` | `scripts/run_batch.py` | the counterfactual — **set it to zero and the agent appears to earn every rupee it touches** |
| `ACTION_COST_PAISE`, `CONTACT_CHURN_HAZARD`, `LTV_HORIZON_MONTHS` | `app/config.py` | what an intervention costs |
| `P_RECOVER_PRIOR` | `app/config.py` | what the agent believes before acting — and the one assumption that is now *scored*, above |
| `VOICE_TRANSCRIPTS` | `scripts/run_batch.py` | what a customer says on a call |

**What is measured here is the mechanism, not the market.** Synthetic cases traverse the
identical code path as a live webhook — in through `intake()`, forward only through
`tick()` — and every interval on this page is sampling uncertainty about that mechanism,
not evidence about Indian subscription recovery.
