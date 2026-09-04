# Sahara — Build Plan

*Razorpay AI Buildathon, Track 03. Six phases that close the measurement gap, fix one
correctness bug, and claim two more example directions — built so every feature we add
arrives with a number attached that no rival submission can produce.*

**State:** 133 tests green, 16/16 acceptance checks passing.

| Phase | What |
|---|---|
| 0 | Where we stand — one defect fixed, one decision open |
| 1 | Measurement floor |
| 2 | Dispatch fencing |
| 3 | Voice · promise tracker · the AI question |
| 4 | Reporting |
| 5 | Frontend (Next.js) |
| 6 | Ship |

---

## Phase 0 — Where we stand

### The control-arm defect, fixed ✅

`scripts/run_batch.py` rolled self-cure for control cases **only**. The treated arm
recovered through interventions, the control arm through self-cure — two different
generative processes, not one world under two policies. Every lift number computed from
them was biased.

Now `roll_self_cure()` iterates `all_subs`. Self-cure is a property of the world, so both
arms get it.

| Metric | Before | After |
|---|---|---|
| Net incremental | ₹7,762 | **₹11,602** |
| Net incremental 95% CI | [−9,473, +20,460] ❌ spans zero | **[+4,223, +19,550]** ✅ |
| Lift | +46.8 pp | +42.1 pp, CI [+23.1, +60.0] |
| Recovery treated / control | 63.5% / 16.7% | 61.5% / 19.4% |

The lift fell 4.7 pp — the treated arm had been denied its organic recoveries — and the
interval tightened enough to clear zero. Both headline numbers now exclude zero at n=88.
Written to a scratch DB; `recovery.db` untouched.

### Correction to an earlier claim

I previously said our control arm was a straw man measured against "do nothing," and
recommended building a third platform-default arm. **That was wrong.** `run_batch.py:45`
documents `BASELINE_RECOVERY_PROBABILITY` as *"the customer tops up, or the issuer stops
declining, and Razorpay's own retry then succeeds."* Our control already models
Razorpay's native retry. It is the platform-default baseline, only under-labelled.

**The third arm is cancelled** — it becomes a docs change in task 1.4.

### Open decision: the frontend ⚠️ needs your call

You asked for React or Next.js. The existing dashboard is **3,302 lines** of working
vanilla HTML/CSS/JS across nine sections, already wired to **21 API routes**. Reaching
parity in Next.js would roughly double the size of the project — and it moves none of the
four things the brief actually grades.

**Option A — Next.js after Phase 4 (recommended).** Backend claim locked first,
then a full App Router rebuild against the same API. You get the modern frontend and the
bar stays protected. If we run out of room, we ship the vanilla dashboard and lose nothing
that is graded.

**Option B — Hybrid.** Keep the vanilla dashboard. Build only the *new* surfaces in
React — approval queue, voice/promise timeline. Cheapest path to "it uses React," but two
stacks to maintain and a visual seam between them.

**Option C — Next.js first (not advised).** Frontend before measurement. This is the
failure mode of the widest rival in the field, whose own evaluation report shows its
agent losing to naive retry by 19% while shipping Three.js and voice synthesis.

---

## Phase 1 — Measurement floor

Lock the fix, then scale until the intervals are unarguable. Everything downstream is
measured against this run.

| # | Task | Files |
|---|---|---|
| 1.1 | **Arm-balance acceptance check.** Assert self-cure roll counts are proportional across arms within binomial tolerance. The test that stops the defect regressing. | `scripts/run_batch.py` → `acceptance_checks()` |
| 1.2 | **Report organic vs intervention-driven recovery per arm.** Add `organic_treated` / `organic_control` to the incremental block, so a reader can see the arms are balanced rather than take it on faith. | `app/metrics.py` → `incremental_recovery()` |
| 1.3 | **Rerun at n = 2,000.** `LLM_PROVIDER=none`, seed 42, holdout 0.35. Costs wall clock and nothing else. Our nearest rival reports n = 900. | `scripts/generate_synthetic.py`, `scripts/run_batch.py` |
| 1.4 | **Relabel the control arm.** State that control reproduces Razorpay's own retry plus self-cure — not silence. Pre-empts "are you beating Razorpay or beating nothing?" | `README.md`, `docs/11-measurement.md` |
| 1.5 | **Metric hierarchy** in a new `docs/analysis-plan.md`. One primary metric, one secondary, everything else descriptive — with an honest note that it was written after the first runs, not before. | `docs/analysis-plan.md` |

**Acceptance:**
- 1.1 → a new `[PASS]` line; deliberately unbalancing the roll turns it red
- 1.2 → both counts printed in the batch summary and served by the metrics API
- 1.3 → CI excludes zero on both lift and net incremental; all acceptance checks pass
- 1.4 → README names what the control arm models, with the config constant cited
- 1.5 → the file exists and the README's headline is the declared primary

> **Known consequence.** Rolling self-cure for every case changes the seeded RNG stream, so
> **every committed number moves**. Unavoidable for a measurement fix. Handle it the way the
> strongest rival handles theirs: keep the superseded output file unedited in the repo and
> record the diff and its cause, rather than quietly overwriting.

---

## Phase 2 — Dispatch fencing

There is **no re-fetch anywhere** in `app/executor.py`. Between deciding and acting, the
customer may already have paid. This is a correctness gap, not a feature gap — today we
could dun someone who settled an hour ago. A rival's headline metric is literally
*"outreach to already-charged customers: 0"*, a claim we cannot currently make.

| # | Task | Files |
|---|---|---|
| 2.1 | **Look before you leap.** `guard_dispatch()` re-fetches the subscription immediately before any contact action. Anything not halted blocks the cycle; the caller takes no further action. | `app/fencing.py` *(new)*, `app/executor.py` |
| 2.2 | **Verify after write.** Re-fetch after a payment link exists. If the world moved, best-effort cancel the link and *always* append a compensation entry — success or failure, the attempt is the evidence. | `app/fencing.py`, `app/audit.py` |
| 2.3 | **Stale-inference guard.** SHA-256 fingerprint over *decision-relevant fields only* — status, attempt count, current period, link presence — snapshotted before the model call and rechecked after. Irrelevant payload churn must never trip it. | `app/fencing.py` |
| 2.4 | **Publish the metric with its denominator.** "Outreach to already-settled customers: 0 of N dispatches fenced." A zero with no denominator attests to nothing. | `app/metrics.py`, dashboard |

**Acceptance:**
- 2.1 → new stop status `stopped_already_settled`, with a test driving a mid-flight settlement
- 2.2 → a `compensation` audit entry type, asserted by test, visible on the case timeline
- 2.3 → a test proving notes/timestamps/customer-metadata changes leave the hash stable
- 2.4 → the figure on the dashboard and in the README, traceable to case ids

> **Design rule.** A fence degrades, never raises. Any transport fault inside a fence is
> caught, logged, and reported as data — a fence that can crash the pipeline is worse than
> no fence. Test-mode rate limits make this a real path, not a theoretical one.

---

## Phase 3 — Voice · promise tracker · the AI question

**One workstream, not three.** A Hinglish call is the channel where someone says *"salary
aane ke baad Friday ko kar dunga."* Turning that into a dated, tracked promise closes
example direction #7 — and it is precisely the job rules cannot do, which finally gives
the LLM ablation something real to measure.

### 3a — The action

| # | Task | Files |
|---|---|---|
| 3.1 | **Register `VOICE_CALL` as an action, not a channel.** Add to `ACTIONS`, `CONTACT_ACTIONS`, and `ACTION_COST_PAISE` at 2500 paise — ₹25, between the ₹12 link and the ₹40 human handoff. Add `P_RECOVER_PRIOR` rows. | `app/config.py` |
| 3.2 | **Substitute at attempt 3.** `card_expired`, `issuer_declined` and `authentication_failed` currently jump straight from a silent link to occupying a person. Voice fills that rung; handoff still closes the case on lapse. | `app/config.py` → `POLICY` |
| 3.3 | **Executor handler.** `place_voice_call()` composes from a registered template, simulated by default, recorded through the existing `ExecutionRecord` interface. Reuses the promise-to-pay grace/lapse pattern already proven in `send_promise_offer`. | `app/executor.py` |
| 3.4 | **Hindi and Hinglish templates** through the same `test_copy_validation.py` gate as every other outbound string. No rival validates outbound copy at all. | `app/config.py`, `tests/test_copy_validation.py` |
| 3.5 | **I8 — verified-recipient allowlist, fail closed.** A real transmission may only target a number on an explicit allowlist; anything else refuses. Synthetic customers can never be dialled, by construction. | `app/invariants.py` |

**Acceptance:**
- 3.1 → voice inherits I2 cooldown, I5 quiet hours, I6 suppression, I7 ceilings and
  annoyance pricing with **zero new guardrail code**. That inheritance is the entire
  argument for this design.
- 3.2 → `MAX_ATTEMPTS` stays 3 and I1 is untouched — `STOP_HANDOFF` is in
  `NON_INTERVENTION_ACTIONS`, so it never consumed an attempt
- 3.3 → voice executions appear in `execution_modes()` distinctly from link sends
- 3.4 → every language variant passes copy validation; an unregistered variant fails the build
- 3.5 → new stop `stopped_unverified_recipient`; a test proves a synthetic number is refused

### 3b — The model gets ears, not a mouth

| # | Task | Files |
|---|---|---|
| 3.6 | **`InboundReading` schema.** Closed-enum intent plus an optional promised date. **No amount field.** A compromised model — or a caller who talks their way into one — cannot make the phone state a wrong rupee figure, because there is nowhere for the figure to travel. | `app/llm.py`, `app/inbound.py` *(new)* |
| 3.7 | **Promise tracker.** Persist the date the customer actually named, schedule to it, sweep on lapse, resolve as kept or broken. Today `send_promise_offer` sets a 72 h deadline but never captures a stated date — it is a promise *offer*, not a tracker. | `schema.sql`, `app/cases.py`, `app/metrics.py` |
| 3.8 | **LLM ablation: rules vs model on promise extraction.** A keyword baseline written in good faith, scored against the model on intent, date, and — the column that matters — whether the reading produces the same policy facts. Paired McNemar test. `LLM_API_KEY` is already set. | `scripts/ablate_inbound.py` *(new)* |
| 3.9 | **Optional: one real call.** Plivo trial behind I8, single demo case, execution mode `plivo_trial_verified` recorded distinctly so metrics can never count a demo call as outreach. Off by default, never the batch path. **Cut first if scope tightens.** | `app/executor.py` |
| 3.10 | **Measure the rung.** Batch with voice, batch without, report the delta with a CI. | `scripts/run_batch.py` |

**Acceptance:**
- 3.6 → a test asserting the schema rejects any amount-bearing field
- 3.7 → schema migration; `promises_kept` / `promises_broken` as traceable metrics
- 3.8 → a number reported in **either** direction. If the model loses, we publish that; a
  negative result honestly kept is worth more than a flattering one.
- 3.9 → a call SID in the hash-chained trail
- 3.10 → the sentence below

> **The output of this phase:**
>
> *"Hinglish voice as escalation rung 3 moved incremental recovery +X pp, 95% CI [a, b], at
> ₹25 per call against ₹40 per human handoff — across 2,000 cases, every call gated by
> I1–I8, priced before dialling, and hash-chained."*
>
> The widest rival has voice but no control arm, so their voice feature is unmeasurable.
> The strongest rival has the measurement apparatus but deliberately left voice unwired.
> Nobody else can produce this sentence.

---

## Phase 4 — Reporting

Three of five rivals score their probabilities. We score none. Cheap to fix, and it
converts our economics from assumption to measurement.

| # | Task | Files |
|---|---|---|
| 4.1 | **Per-category lift with CIs** — including categories where voice adds nothing. Publishing where the agent is flat is what makes the rest believable; the strongest rival keeps two such results deliberately. | `app/metrics.py` |
| 4.2 | **Prior calibration.** Brier score, ECE and a reliability table scoring `P_RECOVER_PRIOR` against realised outcomes. These priors drive every EV gate and have never been checked against reality. | `app/metrics.py`, `app/economics.py` |
| 4.3 | **Promote suppression to the headline.** "N cases deliberately not contacted" belongs in the summary, not buried in `stopped_by_status()`. What the agent declined to do is our strongest story. | `app/metrics.py`, `README.md` |
| 4.4 | **Per-category precision / recall / F1** replacing the bare "100% (84/84)". A table that shows texture reads as more credible than a perfect scalar. | `scripts/run_batch.py` |
| 4.5 | **Latency p50 / p95** per pipeline stage. Currently unreported. | `app/metrics.py` |

**Acceptance:**
- 4.1 → a lift table split by failure category, arm counts shown
- 4.2 → "prior said 0.45, realised 0.42" per category-action pair
- 4.3 → the figure in the README results table
- 4.4 → support counts per category alongside each score
- 4.5 → percentiles in the batch summary

---

## Phase 5 — Frontend · Next.js

*Gated on the Phase 0 decision. Written assuming Option A.*

The API is already the contract — 21 routes, all JSON, no server-rendered HTML. The
rebuild is a pure frontend swap with no backend churn, and the vanilla dashboard keeps
working the whole time as the fallback.

| # | Task | Files |
|---|---|---|
| 5.1 | **Scaffold.** Next.js App Router, TypeScript, Tailwind. Port the existing token set from `dashboard/style.css` verbatim — the palette is already considered and the held-out arm already has its own colour throughout. | `web/` |
| 5.2 | **Typed API client** generated from the FastAPI OpenAPI schema, so a route rename breaks the build instead of the page. | `web/lib/api.ts` |
| 5.3 | **Server components for read paths** — summary, categories, mechanism, cases. Client components only for the control room, which polls jobs. | `web/app/` |
| 5.4 | **New surfaces we do not have today:** the approval / handoff queue (we charge ₹40 a case and have no screen for it), the voice-and-promise case timeline, and the fencing compensation log. | `web/app/queue`, `web/app/cases/[id]` |
| 5.5 | **Parity gate.** The vanilla dashboard is deleted only once every one of its nine sections has a Next.js equivalent. Until then both ship. | — |

---

## Phase 6 — Ship

We have no Dockerfile, no CI, and no deployed demo. Two rivals have all three. Judges
click links.

| # | Task | Files |
|---|---|---|
| 6.1 | **Dockerfile and CI.** GitHub Actions running `pytest` plus the batch acceptance checks on every push. | `Dockerfile`, `.github/workflows/ci.yml` |
| 6.2 | **Deploy with a fail-closed public demo mode.** Every write route disabled; boot *refuses* if any provider credential is present. Seeded, sanitised data only. | `app/config.py`, `app/control.py` |
| 6.3 | **`VERIFY.md` plus a drift guard.** Every headline claim mapped to the artifact that proves it and the command that re-proves it, with `scripts/verify_numbers.py` re-running from seed and byte-comparing against committed JSON. | `VERIFY.md`, `scripts/verify_numbers.py` |
| 6.4 | **`THREAT-MODEL.md` non-capabilities table.** Each danger against the reason no code path exists for it. Pure documentation over guardrails we already enforce — the strongest security property is everything the system cannot do. | `THREAT-MODEL.md` |

**Acceptance:**
- 6.1 → a green badge in the README
- 6.2 → a live URL; `POST` to a control route returns 404; booting with `RAZORPAY_KEY_ID`
  set exits nonzero
- 6.3 → editing any digit in the README results block makes CI exit nonzero
- 6.4 → every row cites the invariant or the absent module

---

## What we are deliberately not building

Each of these is a defensible refusal, and saying so is stronger than a shallow
implementation.

### Checkout drop-off recovery — example direction #2

**I4 kills it.** Our agent never acts on an unknown cause — one of four hard stops.
Checkout abandonment is almost entirely unknown cause: there is no decline code, and "why
did they leave" is unobservable. We would either stop every case or weaken I4, and I4 is
part of why our stopping-rules story is the strongest in the field.

Secondary: abandonment is a non-event with no signed webhook; consent flips from
post-purchase service to pre-purchase marketing; and an abandoned cart is money
*hypothetical*, so the EV gate would be pricing an imaginary receivable.

### B2B receivables chaser — example direction #4

**There is no event.** Detection is `webhooks.intake()` on three failure types; an overdue
invoice generates nothing, because nothing failed. Overdue is a state you discover by
polling a due date.

Beyond that: `MAX_ATTEMPTS = 3` at 12/24/72 h and I2's 24 h cooldown are calibrated to a
days-long window, while receivables ladders run weeks to months; and our six categories
are card decline reasons, where an overdue invoice has causes like "awaiting PO approval."

New detector, new diagnosis enum, new policy table, retuned invariants — a second project
sharing our spine, which `docs/18` already documents as a NEW → REUSE path.

### Also skipped

- **Live red-team console and chaos drills.** We have the substance — I1–I8, 133 tests —
  without the theatre. Real, but it wins no bar clause.
- **Oracle upper bound, MLflow tracking, hierarchical Bayesian model.** Strong in rival
  repos; none of them moves measured money, compliant escalation, stopping rules, or the
  audit trail.
- **Learned retry timing.** `docs/18` already gives the reason: a wrong learned policy
  moves money wrongly, and the deterministic table is the baseline it would have to beat.
- **Pre-registration.** Genuinely unavailable — claiming it retroactively would be
  dishonest. Task 1.5 gets the defensible half.

> **The trap this list exists to avoid.** The widest submission in the field runs
> LangGraph, a Java ledger service, React 19, Three.js and voice synthesis across 38 MB.
> Its own committed evaluation reports the agent recovering ₹81,846 against ₹100,998 for
> retry-everything — **losing to the dumbest possible baseline by 19%** — with no control
> arm, no confidence interval anywhere, and 11 tests. Coverage is not the win.

---

## Risk register

| Risk | Handling |
|---|---|
| **Every committed number moves** once self-cure enters both arms. | Expected and unavoidable. Keep the superseded report unedited, record the diff and its cause — the same discipline the strongest rival applies to its own correction. |
| **Voice rung collides with I1** if it is added rather than substituted. | Substitute at attempt 3. `STOP_HANDOFF` is a non-intervention, so it never held an attempt slot. `MAX_ATTEMPTS` stays 3. |
| **The ablation flatters nobody** — the model may lose at promise extraction. | Publish it. A measured negative is the single most credible thing in a hackathon submission, and it is what separates us from the field. |
| **DLT registration** blocks any compliant call to a real customer. | Never in scope. I8 makes the boundary structural rather than a caveat: the real-send path cannot dial a synthetic customer. |
| **Next.js crowds out the work the bar needs.** | Option A sequences it after Phase 4, and the vanilla dashboard stays shippable until parity. This is the risk the plan is ordered around. |
| **Razorpay test-mode limits** during fencing re-fetches. | Fences degrade to a logged no-op and never raise. The ledger records what was and was not verified. |

---

## Sequence

Phases 1 → 2 → 3 → 4 are **the claim**. Phase 5 is the frontend you asked for, sequenced
so it cannot eat the claim. Phase 6 is what makes it clickable.

**Awaiting:** your call on the Phase 0 frontend decision. Then Phase 1 starts with the
arm-balance check.
