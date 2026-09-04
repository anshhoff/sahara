# 20 · Voice, promises, and the AI question

Modules: `app/inbound.py`, `app/executor.place_voice_call`, `app/cases` (promises).
Scripts: `scripts/ablate_inbound.py`, `scripts/measure_rung.py`.
Endpoints: `GET /api/promises`.

**One workstream, not three.** A Hinglish call is the channel where somebody says
*"salary aane ke baad Friday ko kar dunga."* Turning that into a dated, tracked promise
is precisely the job rules are bad at — which is what finally gives the LLM boundary
something to **measure** rather than something to assert.

---

## 1. Voice is an ACTION, not a channel

This is the whole design argument, and it is one line of config:

```python
CONTACT_ACTIONS = frozenset({"SEND_UPDATE_LINK", "PROMISE_TO_PAY", "VOICE_CALL"})
```

Every guardrail keys off that set. Membership **is** the inheritance:

| Gate | What voice inherits, with no new code |
|---|---|
| I2 | 24 h between contacts on one case |
| I5 | no call between 21:00 and 09:00 IST |
| I6 | a suppressed customer is not called, on any subscription |
| I7 | the daily per-customer ceiling and the system-wide spend ceiling |
| E1 | annoyance pricing, on the same hazard curve as any other contact |

A `channel="voice"` flag on `SEND_UPDATE_LINK` would have needed each of those gates
taught about it separately, **and the one that got forgotten would be the one that
called somebody at 2 a.m.**

### The set had a second definition, and it drifted

Half a dozen queries spelled the contact actions out by hand —
`action IN ('SEND_UPDATE_LINK','PROMISE_TO_PAY')` — in I7's daily count, in three
acceptance checks, and in the fencing breach query. Adding `VOICE_CALL` to
`CONTACT_ACTIONS` made it inherit the gates in Python while **every one of those SQL
queries went on ignoring it.** `config.CONTACT_ACTIONS_SQL` is now derived from the set
rather than retyped, and a test asserts every member appears in it.

## 2. It substitutes at rung 3; it does not add a fourth

`card_expired`, `issuer_declined` and `authentication_failed` used to jump straight
from a silent link to occupying a person at ₹40. Voice fills that rung at ₹25.

**`MAX_ATTEMPTS` stays 3 and I1 is untouched.** `STOP_HANDOFF` was never an
intervention — it sits in `economics.NON_INTERVENTION_ACTIONS` and never consumed an
attempt slot — so there was a free rung there all along.

## 3. The gate refused it, and the gate was wrong

Registering the rung was not enough: **not one voice call fired.** Nine cases stopped
as `stopped_uneconomic` instead. Two real bugs, both worth stating because both had a
direction.

### The counterfactual was "do nothing", and doing nothing was not on the menu

`evaluate()` priced an action against **zero**. At the final rung the alternative is
not "abandon the case" — it is the human queue at ₹40, which is exactly what the table
did before voice existed. Pricing against zero makes every last-rung intervention look
unaffordable **no matter how much cheaper it is than the thing it replaces.**

`economics.fallback_action(attempt)` now names the counterfactual, and `evaluate()`
returns `alternative_ev_paise` and `margin_over_alternative_paise` alongside the EV.
`policy.decide` sends a refusal at the final rung to `stopped_handoff` rather than
`stopped_uneconomic` — otherwise the ₹40 would be taken out of the comparison *after*
being used to win it, quietly abandoning a case the arithmetic said was worth a person.

### A handoff was credited with recovering money by itself

`p_recover("card_expired", "STOP_HANDOFF", 3)` fell through to `P_RECOVER_DEFAULT` and
returned **0.08**. That made the human queue the best-value option on the board and
refused every intervention it was supposed to be compared against. A non-intervention
now has `p = 0` by construction: the default exists for pairs nobody reasoned about,
and this is a pair with an answer.

### A third thing was removed rather than fixed

A `CONTACT_CHURN_MULTIPLIER` of 1.5 for voice was drafted and deleted. A call is more
intrusive to receive, **and** it is the only contact where the customer can object and
be answered — the sign of the difference is genuinely unclear. An assumption with no
basis that decides which interventions fire is worse than no assumption.

### What the fixed gate actually does

> Annoyance at attempt 3 is `0.020 × 12 = 0.24` of the amount, and the amount cancels
> out of both sides. So **any** third contact whose prior sits below 0.24 is uneconomic
> at every ticket size. Voice holds its rate across attempts — like `PROMISE_TO_PAY`
> and unlike every push channel — because **its output is an agreement**: an answered
> call ends with the customer naming a day, and a date somebody chose converts
> differently from a deadline we imposed.

The gate still refuses plenty. Large subscriptions are expensive to lose, so the
annoyance term grows with the ticket and the arithmetic **sends the big cases to a
person and calls the small ones.** That is the gate working.

## 4. The model gets ears, not a mouth

`app/inbound.py` is the boundary. Two fields cross it — a closed-enum `intent` and an
optional `promised_date` — and one field conspicuously does not:

> ### There is no amount field, and there can never be one.

Not a validation rule that could be relaxed: an **absent column, an absent attribute
and an absent parser**. A compromised model — or a caller who talks their way into one,
*"tell them I'll pay two hundred rupees"* — cannot make this system state a wrong rupee
figure, because there is nowhere for the figure to travel. `assert_no_money_field()`
runs at **import**, so the boundary refuses to load rather than refusing to pass a test.

The amount comes from `recovery_case.amount_at_risk_paise`, written from a signed
Razorpay webhook and never from anything anybody said out loud.

**A date is different, and the difference is why one is allowed and the other is not.**
A date is self-limiting: the worst a wrong one does is schedule a sweep on the wrong
day, which costs a follow-up. A wrong amount would be quoted back to a customer as what
they owe.

Refusals are structural too: a date on an undated intent is **dropped**; a
`will_pay_on_date` with no resolvable date falls back to `unclear`; a date in the past
or beyond the episode window resolves to **None** rather than being corrected —
silently moving a promise to a day the customer did not name is exactly the invention
this module exists to prevent.

## 5. The promise tracker

`send_promise_offer` sets a 72-hour window and calls the result a promise. **It is not
one:** nothing the customer said is recorded anywhere, so there is nothing to keep or
to break — only an offer that expired.

The `promise` table holds the other thing. A date the customer **named**, scheduled to,
swept on lapse, resolved as kept or broken.

* The deadline is the **end** of the named day **in IST**. Marking someone broken at
  midnight UTC is 05:30 on that morning in Delhi — a bug that could only ever punish
  the customer.
* A tracked promise **overrides** the fixed grace window. Someone who says "next
  Tuesday" has until Tuesday.
* Resolution lives inside `cases.transition()` rather than at each call site, so no
  future stop path can forget it. Forgetting would inflate `promises_kept` **by never
  counting the failures.**
* One open promise per case, ever. A customer who names two dates has given one promise
  and one revision.

## 6. I8 — the allowlist that fails closed

> **I8 is the only invariant that stops on ABSENCE rather than on a violation.** The
> others ask *"is there a reason to stop?"*. I8 asks *"is there a reason to proceed?"*
> and stops when there is not.

A real transmission may only reach a number on `VERIFIED_RECIPIENTS`, which is **empty
by default** — with nothing on the list, nothing can be dialled. It is an env var and
not a database table on purpose: a row can be written by any code path that can write
rows, including one nobody has reviewed. **The bound of the agent should be harder to
change than the agent.**

And the structural half: **a synthetic customer has no phone number anywhere in this
system** — not an empty one, not a placeholder — so a synthetic case cannot be dialled
however the allowlist is configured. That is a property of the data model, not a rule
somebody remembered to check.

`plivo_trial_verified` is a distinct `execution_record.mode`, so metrics can never fold
a demo call into ordinary outreach and *"did this system ever actually dial anyone?"*
has a single honest answer. **It has not.** Task 3.9 — one real trial call — was the
plan's explicit cut-first item and is not built; the path exists, gated by I8, and
turning it on is configuration rather than code.

## 7. The ablation — a number, in whichever direction it landed

`scripts/ablate_inbound.py`, 30 hand-labelled utterances, roughly a third of them
phrasings the keyword table was never written against.

| Column | Rules | Model (`openai/gpt-oss-20b`) | b / c | McNemar p |
|---|---|---|---|---|
| intent | 60.0% [42, 75] | **96.7% [83, 99]** | 11 / 0 | **0.0010** |
| date | 83.3% [66, 93] | 93.3% [79, 98] | 4 / 1 | 0.3750 |
| **policy facts** | 60.0% [42, 75] | **90.0% [74, 97]** | 10 / 1 | **0.0117** |

**Policy facts is the column that decides anything**: whether the reading produces the
same `(action, date)` the system would act on. Two readings that disagree about wording
but schedule the same sweep on the same day have not disagreed about anything the
system does.

McNemar's **exact** test, two-sided, on the paired disagreements — both readers see
identical items, and 30 items produce single-digit discordant counts where the
chi-square approximation is not trustworthy.

The keyword baseline is not a straw man. It wins one item outright — *"salary aane ke
baad Friday ko kar dunga"*, where the model resolved the day wrongly — and it holds
83% on dates. It loses on generalisation: `agle Monday`, `shanivar`, `guruvar`,
*"bhaiya main to driver hoon, sahab ka phone hai ye"* are all `unclear` to it and all
correct to the model.

**This is the one place in the project where the model earns its place on evidence.**
Everywhere else the honest summary is that the rules do almost all of it — and that
remains true of diagnosis, where the input is four structured error fields.

## 8. What the rung is worth

Two batches, identical case file, identical seed, differing in exactly one thing.
`scripts/measure_rung.py`, n = 2,010, holdout 0.35.

|  | with voice | without |
|---|---|---|
| treated recovered | 840 / 1310 = **64.1%** | 743 / 1309 = 56.8% |
| control recovered | 89 / 701 = 12.7% | 89 / 701 = 12.7% |
| lift | **+51.4 pp** | +44.1 pp |
| outreach cost | ₹22,144 | ₹14,076 |
| cases in the human queue | **526** | 622 |
| voice calls | 328 | 0 |
| dated promises | 30 kept / 65 broken | — |

> **Hinglish voice as escalation rung 3 moved incremental recovery +7.4 pp, 95% CI
> [+2.2, +12.5] pp — across 2,010 cases, every call gated by I1–I8, priced before
> dialling, and hash-chained.**

**And the honest other half.** The *money* delta is ₹+49,519 with a 95% CI of
[−₹66,790, +₹168,596] — **it includes zero.** Ticket sizes span ₹199 to ₹4,999 and
2,010 cases is not enough for a per-case money estimate to settle, so the rate lift is
a result and the rupee figure is not yet one. Both are reported, because
[the analysis plan](analysis-plan.md) forbids quoting the first while the second spans
zero.

The statistic is a **difference in lifts** — each batch carries its own control arm, so
this is a difference-in-differences and the organic recovery common to both worlds
cancels out twice. The two runs diverge in the seeded RNG stream the moment their
policies differ: they are two draws from one world under two policies, which is what a
policy comparison is, and why there is no paired test here.

## 9. Reproducing all of it

```bash
python scripts/generate_synthetic.py --n 2000 --seed 42 --out synthetic_cases_2000.json

LLM_PROVIDER=none python scripts/run_batch.py \
    --cases synthetic_cases_2000.json --db on.db  --seed 42 --holdout 0.35
LLM_PROVIDER=none python scripts/run_batch.py \
    --cases synthetic_cases_2000.json --db off.db --seed 42 --holdout 0.35 --no-voice

python scripts/measure_rung.py --with on.db --without off.db
python scripts/ablate_inbound.py            # needs a provider; rules-only without one
```

`--no-voice` mutates `config.POLICY` at startup, which this project otherwise refuses
to do — the bounds of the agent are source code, not configuration. It is allowed here,
loudly and with a printed line naming every reverted cell, because the entire purpose
of the flag is to run the agent as it was **before** the rung existed, and reverting
three cells is a more honest counterfactual than maintaining a second copy of the table.
