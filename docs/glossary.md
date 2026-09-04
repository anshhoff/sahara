# Glossary

Every term of art in this project, with the file that defines it.

---

### at-most-once
The execution posture: a crash between an irreversible side effect and its record costs an
**audit row**, not a second message to a customer. `executor.execute_decision` claims the
decision before acting, so a crashed execution is never retried.
→ [17 § 6](17-concurrency-and-failure.md#6-crash-consistency--deliberately-at-most-once)

### acceptance check
One of ~16 assertions `run_batch.py` runs over the finished batch, each re-derived **from
the rows** rather than from what the gates believe about themselves. A failure exits
non-zero. → [14 § 5](14-batch-and-synthetic.md#5-the-nine-acceptance-checks)

### annoyance cost
`hazard(attempt) × 12 months × amount` — the modelled chance that this message is the one
that makes the customer cancel instead of pay. It **prices decisions but is never booked as
a cost**, because no rupee leaves the account for it. → [08 § 4](08-economics.md)

### arm
`treated` (the agent works the case) or `control` / `holdout` (detected and diagnosed, then
never intervened on). Assignment is randomised, seeded, and travels on the event payload.
→ [11 § 4](11-measurement.md#4-the-randomised-control-arm)

### at risk (₹)
`Σ amount_at_risk_paise` over all cases — **one charge cycle per case, frozen at creation**.
Deliberately not multiplied by the remaining subscription term, which would be projection
rather than measurement.

### attempt
One *executed* intervention. `STOP_HANDOFF` does not consume one. Capped at 3 by **I1**,
enforced atomically by `cases.reserve_attempt()`.

### audit stage
One of `detect` · `diagnose` · `decide` · `execute` · `stop` · `outcome`.

### actor
Who did it: `system` · **`llm`** · `razorpay` · `human`. Every model contribution is
labelled `llm`, so the trail can be filtered to exactly what the model touched.

### batch mode
Replaying synthetic payloads through the real pipeline on a `SimulatedClock`, so a 14-day
episode resolves in milliseconds. → [14](14-batch-and-synthetic.md)

### bootstrap CI
A percentile confidence interval over 10,000 case-level resamples, seeded and therefore
deterministic. **One resample drives both the rate and money intervals**, so they describe
the same simulated batches.

### case file
`GET /api/cases/{id}` — the case, its events, diagnoses, decisions with nested executions,
and the complete audit trail. **This response *is* the handoff artifact a human picks up**,
which is why a handoff is charged ₹40.

### category
One of six fixed failure causes: `card_expired` · `insufficient_funds` · `issuer_declined`
· `authentication_failed` · `invalid_payment_method` · `unknown`. Guarded by four layers
ending in a SQLite `CHECK`. → [05 § 1](05-diagnosis.md)

### claim token
A conditional statement exactly one caller can win, applied atomically by SQLite. Three
exist: the `UNIQUE` event insert, the `status='scheduled'` decision update, and
`reserve_attempt`. → [17 § 1](17-concurrency-and-failure.md)

### contact action
`SEND_UPDATE_LINK` or `PROMISE_TO_PAY` — the actions that put a message in front of a
person, and therefore the set gated by I2, I5 and I7. **`RETRY_LATER` is deliberately not
one.**

### control arm → see **arm**

### cooldown
The minimum 24 h between two contacts on one case (**I2**). Enforced by the gate, *not* by
policy delays — which is why spacing holds even if the policy table is wrong.

### copy skeleton
Drafted message text containing **no digits**, only slots (`{AMOUNT}`, `{LINK}`,
`{MERCHANT}`, `{COOLDOWN_HOURS}`, `{PROMISE_HOURS}`). Deterministic code substitutes
authoritative values after validation. → [04 § 5.3](04-pipeline.md)

### deferral
A timing gate's verdict that a contact must wait. Deferrals are **collected**, and the
**latest** target wins — returning on the first would schedule a contact for a moment a
later gate also forbids.

### episode
One recovery attempt-sequence for one subscription — i.e. one `RecoveryCase`. Bounded to
**14 days** (`EPISODE_WINDOW_DAYS`); an open case that outlives it closes rather than
becoming a zombie.

### E1
The economics gate: an intervention whose expected value is negative is never executed.
Deliberately **not** an invariant — it is a business judgement made of estimates.
→ [08](08-economics.md)

### EV / expected value
`p_recover × amount − direct_cost − annoyance_cost`, in paise. Written onto **every**
decision, including the ones that proceeded.

### gate
A check that can stop or defer. Two invariant phases (`pre_decision`, `pre_execution`) plus
gate E1.

### genesis hash
`"0" × 64` — the constant the first audit entry hashes against, so a log truncated to its
first entry does not verify perfectly.

### gross recovery
Total ₹ recovered, ignoring the counterfactual and the cost. **The number that cannot go
down by sending more messages**, and therefore the wrong headline.

### handoff
`STOP_HANDOFF` — closing a case with a complete file for a person. Costs ₹40, contacts
nobody, and **does not consume an attempt**. A deliverable, not a failure.

### head
The `entry_hash` of the most recent audit entry — a one-line fingerprint of one database.
**Not** a reproducibility check: ULIDs carry a random tail, so the same seed gives identical
numbers and different hashes.

### holdout → see **arm**

### I1 … I7
The seven stopping invariants. I1–I4 are safety (per case); I5–I7 are contact hygiene (per
person, on the IST clock). → [07](07-guardrails.md)

### incremental recovery
`(money per assigned treated case − money per assigned control case) × n_treated`. What
came back **because of** the agent. Per-*assigned*-case is what makes the arms subtractable.

### intake
`webhooks.intake()` — **the single ingestion path.** A live delivery and a synthetic payload
differ only in `source`.

### intention-to-treat
Every case counts in the arm it was **assigned** to, whatever status it reached — including
cases the agent refused to act on.

### IST day
A calendar day in UTC+05:30 (no DST). The window I7's ceilings reset on, computed as a UTC
half-open interval so a daily total is an indexed range scan.

### live-link budget
`LIVE_LINKS_MAX`, the per-process cap on real test-mode Payment Links. Spent **before** the
call and **never refunded on failure** — deliberately fail-closed.

### `LLM_PROVIDER=none`
No model at all. Ambiguous cases become `unknown` and stop; copy uses static templates.
**Not a degraded mode to hide — the proof that the guardrails do not depend on model
quality.**

### match input
`lower(error_reason + error_description + error_code)` — the string the rule table scans.

### mechanism, not the market
The standing caveat on every batch number: outcome probabilities are modelling assumptions,
so what is measured is that synthetic cases traverse the identical code path as live
webhooks — not real-world recovery rates.

### net incremental
`incremental_recovery − total_cost`. **The number this project stands behind.** All cost is
subtracted from it, because a control case is never executed against.

### paise
The integer unit of money used **everywhere** internally. ₹1 = 100 paise. No float ever
touches a rupee; conversion happens only at the presentation edge.

### policy row ref
`"card_expired/2"` — the exact `(category, attempt)` cell a decision came from, recorded so
a decision cites its own justification.

### receipts
`intervention_decision.invariant_check` — the per-phase record of which gates ran and what
each returned. `not_applicable` is distinguished from `pass`, because recording a check
that did not run as one that passed would make the trail claim more than it can support.

### recovery signal
`subscription.charged` or `payment_link.paid`. **The only thing that makes a case
`recovered`** — sending a link recovers nothing.

### R1 … R7
The diagnosis rule table. R1–R6 are substring patterns in table order, first hit wins; R7
fires when every error field is empty. `R7-no-llm` is the distinct marker for "the rules
missed and no model was configured".

### seq
The per-case audit sequence, gapless from 1, `UNIQUE(case_id, seq)`.

### simulated clock
`clock.SimulatedClock` — a deterministic clock the batch runner advances one hour per
iteration, feeding the identical `executor.tick()` the live loop calls.

### slot
A placeholder in copy that deterministic code substitutes with an authoritative value.
The mechanism that makes an invented amount **unrepresentable** rather than merely detected.

### storm
The control room's adversarial probe: *n* simultaneous webhook deliveries through
`intake()`, checking that duplicates collapse and that a burst leaves exactly one live
decision.

### strict rate
`recovered / all cases`, reported alongside `rate` (`recovered / closed cases`) — which
preempts the denominator question rather than waiting for it.

### superseded
A decision status: a newer failure event or a recovery signal made it moot before it ran.

### suppression
A row in `suppression`, keyed on `customer_id` — the **person**, not the case. What **I6**
reads. Distinct from `recovery_case.customer_opted_out`, which is per-case and what **I3**
reads.

### synthetic
The provenance flag on every row. `/api/summary` always reports `n_synthetic` / `n_live`
and never blends them.

### tick
`executor.tick()` — one iteration of the loop: execute what is due, close lapsed promises,
close expired episodes. Every 30 s in live mode (`include_synthetic=False`), directly in
batch mode.

### traced metric
A metric implemented as *select the ids, then aggregate*, returning both — which is what
`GET /api/metrics/trace/{metric}` serves.

### ULID
The id format: a 48-bit millisecond timestamp + 80 bits of randomness in Crockford base32,
26 chars, lexicographically sortable. Implemented in `db.py` in ~30 lines rather than as a
dependency.

### unknown
The sixth category, and the system's way of saying *I do not know*. **Never acted on
(I4).** Every model failure mode collapses to it.
