# 19 · Decision log

The consequential design decisions, each with the alternative that was rejected and what
that choice costs. Where a decision is enforced by a test, the test is named.

Format: **decision · alternative · why · cost**.

---

## D-01 · Hand-written SQL, no ORM, no migration framework

**Alternative:** SQLAlchemy + Alembic.

**Why:** the whole schema is eight tables, and every query in this project is one a reader
should be able to check against the metric it claims to compute. An ORM puts a translation
layer between the claim and the check. `db._migrate()` handles the only migration shape
that actually occurs here — additive columns.

**Cost:** the status `CHECK` constraint cannot be widened in place; an old database rejects
newly-added statuses. Documented in [03 § 7](03-data-model.md#7-migrations) as the correct
failure, since a database written before those states existed has no case that could
legitimately be in one.

---

## D-02 · No job queue; a `scheduled_for` column and a 30-second scan

**Alternative:** Celery / RQ / a broker.

**Why:** the system is a state machine over ~100 rows whose steps are separated by *hours*.
A queue adds delivery semantics, a broker, and dead letters to buy throughput nobody needs
— and it would put the strongest correctness claim (a decision executes **at most once**)
behind someone else's at-least-once guarantee.

**Cost:** up to 30 seconds of latency on a due decision, and a scan instead of a push.
`idx_decision_due` covers the scan.

---

## D-03 · An injectable clock; nothing calls `datetime.now()`

**Alternative:** freeze time in tests with a library; run the batch in real time.

**Why:** it is what lets a 14-day recovery episode resolve in milliseconds **through the
same code path** a live webhook takes. The batch numbers become a claim about the
production pipeline rather than about a parallel harness.

**Cost:** the two clocks share one database, which required `include_synthetic=False` on
the background loop — a subtlety that would otherwise silently destroy a finished
experiment. Pinned by `test_background_tick_leaves_batch_rows_alone`.

---

## D-04 · The batch talks to the pipeline through `intake()` and `tick()` only

**Alternative:** the runner writes application rows directly, which is faster to write and
easier to control.

**Why:** it is the *entire* basis for claiming the batch measures the real pipeline. A
synthetic payload and a live delivery differ only in `source`.

**Cost:** the runner has to model outcomes as *webhook payloads delivered later*, which is
more machinery than setting a status. Worth it.

---

## D-05 · Rules first, model only for the residue

**Alternative:** classify everything with the model.

**Why:** the rule table resolves the overwhelming majority of real failures at confidence
1.0, deterministically and free. The model earns its place on exactly the cases rules
cannot reach — and when there is no text to classify, R7 short-circuits without troubling
a model at all.

**Cost:** table order is significant, so adding a rule in the wrong position silently
reclassifies traffic. Pinned by `test_rule_order_decides_an_ambiguous_string`.

---

## D-06 · The invariants live in a module that imports almost nothing

**Alternative:** invariant checks inline in `policy.py` and `executor.py`.

**Why:** `invariants.py` imports `db`, `clock` and `config` — **not** `policy`, `llm` or
`executor`. A bug in the policy table cannot route around a stopping rule, and no model
output can reach one. Enforced by
`test_invariants_module_is_independent_of_policy_and_the_model`.

**Cost:** the module re-loads the case from the database on every check rather than taking
it as trusted input. That is also the feature — see D-08.

---

## D-07 · Gate E1 is *not* an invariant

**Alternative:** one list of eight rules.

**Why:** I1–I7 are safety and consent — bounds and facts, non-negotiable. E1 is a business
judgement made of **estimates**. Keeping them in different modules means a bad estimate
can cost money and **cannot cost someone their consent**. The dashboard shows them in one
panel but labels E1 a different `kind`, and the wording says why.

**Cost:** two mechanisms where one would do, and a reader has to learn the distinction.

---

## D-08 · Every gate reloads the case; both phases run

**Alternative:** check once, at decision time, and trust the decision.

**Why:** hours pass between a decision and its execution. An opt-out can land in that
window; night can fall; another case can contact the same person. The pre-execution
re-check exists precisely to catch state that changed after the decision was made.

**Cost:** two database reads per action, and receipts that must distinguish `pass` from
`not_applicable` — because recording a check that did not run as one that **passed** would
make the trail claim more than it can support.

---

## D-09 · Deferrals are collected; the latest target wins

**Alternative:** return on the first gate that defers.

**Why:** returning early would schedule a contact for a moment a later gate also forbids —
**and that bug only shows up at 3 a.m.** Pinned by
`test_i5_and_i2_together_pick_the_later_target_not_the_first_one`.

**Cost:** every timing gate must be evaluated even when an earlier one already deferred.

---

## D-10 · I1 is reported by the gate but enforced by a conditional `UPDATE`

**Alternative:** trust the gate's read of `attempt_count`.

**Why:** a read cannot hold a cap against a concurrent execution. **This was a real bug** —
two paths both read 2 and both wrote 3, a lost update; the counter under-counted, I1
under-fired, and two interventions shipped. `cases.reserve_attempt()` is one atomic
conditional `UPDATE`.

**Cost:** the enforcement point is not where the rule is documented, so the module docstring
has to say so explicitly.

---

## D-11 · The event row is written before the case

**Alternative:** create the case, then store the event.

**Why:** the `UNIQUE` event id makes the insert itself the dedupe — it is the claim token.
Opening the case first would mean a lost race leaves an **empty case behind: a second
attempt budget against a customer who only ever failed once.**

**Cost:** `failure_event.case_id` must be nullable and receives exactly one post-insert
write, which is the only mutation that table ever sees.

---

## D-12 · At-most-once execution

**Alternative:** at-least-once, retrying anything without a recorded execution.

**Why:** on a crash between the side effect and its record, at-most-once costs an **audit
record** — recoverable. At-least-once costs a **second message to a customer** — not
recoverable.

**Cost:** orphaned decisions exist. An at-most-once system has to be able to find what it
lost, so a one-query orphan detector is part of the design and is tested.

---

## D-13 · The audit chain spans the whole log, not per case

**Alternative:** a chain per case, which is simpler and parallelisable.

**Why:** a per-case chain catches an edit inside a trail and misses the **deletion of an
entire trail** — the more attractive thing to delete, because it removes the inconvenient
number from the metrics as well as its explanation.

**Cost:** all audit writes serialise on one lock, and the chain must be verified in
insertion order.

---

## D-14 · Unchained rows are reported, never skipped

**Alternative:** ignore rows written before the chain existed.

**Why:** an unverifiable entry silently folded into a passing result is exactly the thing
`verify()` exists to stop. A gap breaks the anchor, so the next chained row's predecessor
is **adopted rather than checked** — pretending otherwise would report a break where there
is only missing evidence. The gap itself is `n_unchained`.

**Cost:** `verify()` has three outcomes instead of two, and readers must understand the
difference.

---

## D-15 · Copy is a slot skeleton the model may not put digits in

**Alternative:** let the model write the amount and check it matches.

**Why:** the earlier design did exactly that, and it rejected every otherwise-good sentence
mentioning any other number ("within 24 hours", "attempt 2 of 3"). Slots are strictly
safer — **a model that cannot type a digit cannot invent one** — and they let copy cite the
bounds the system actually enforces.

**Cost:** the prompt is more constrained, and every static template must also be written as
a skeleton. Enforced by `test_every_static_template_passes_its_own_validator`.

---

## D-16 · The agent's priors are deliberately not the simulator's outcome model

**Alternative:** one shared table, which is less to maintain.

**Why:** if they were equal the agent would be scoring its own decisions with the answer
key, every EV would be correct by construction, and the whole measurement would be
circular. Two tests enforce it, and the second is the load-bearing one: **a value test can
be satisfied by nudging a number; an import grep cannot**
(`test_nothing_in_app_imports_the_batch_simulator`).

**Cost:** two tables of numbers that look similar and must never be reconciled — which is
exactly why the reason is written in both files.

---

## D-17 · A randomised control arm, and incremental reported net of cost

**Alternative:** report gross recovery, like every dunning tool.

**Why:** gross recovery **cannot go down by sending more messages**, which makes it the
wrong thing to optimise. Without a control arm, rupees that would have come back anyway
are silently credited to the agent.

**Cost:** 35% of cases are deliberately not worked, the headline number is smaller, and its
confidence interval is honest enough to span zero on money. All three are stated rather
than smoothed.

---

## D-18 · The holdout branch sits *after* the invariant gate

**Alternative:** short-circuit control cases immediately, which is simpler.

**Why:** a holdout case that opt-out or an unknown cause would have stopped is recorded
under **that** reason, because the same stop would have happened in the treated arm.
Keeping the reasons intact is what keeps the arms comparable.

**Cost:** control cases run the gate for a decision that will never be made — and the
counterfactual EV is computed and audited too, so the arm's cost is measurable.

---

## D-19 · Intention-to-treat

**Alternative:** exclude cases the agent refused to act on.

**Why:** dropping them would flatter the result by exactly the cases the agent handled most
conservatively.

**Cost:** the treated arm's rate includes cases the agent deliberately did nothing on,
which lowers the reported lift. Correct.

---

## D-20 · Every metric returns `(value, case_ids)`

**Alternative:** aggregate queries returning a number.

**Why:** there is **structurally no way** to compute a headline number without materialising
its member set, which is what makes `GET /api/metrics/trace/{metric}` possible and what
makes "trace any number to its cases" a property rather than a promise.

**Cost:** slightly more work per metric, and `_sum_amount` has to stage ids in a temp table
and join rather than expanding an `IN (…)` list — because SQLite caps bind parameters and
the obvious version fails on the first merchant with a thousand cases.

---

## D-21 · The dashboard renders `/api/mechanism`, never a copy of the rules

**Alternative:** hardcode the invariants and the policy table in the front end.

**Why:** **a gate that is described in one place and enforced in another eventually
describes something the code no longer does.** The plain-English gloss lives in `api.py`
next to the machine rule text for the same reason.

**Cost:** one more endpoint, and the front end cannot render the rules offline.

---

## D-22 · The control room shells out to the real commands

**Alternative:** re-implement the batch and the tests in-process for a nicer UI.

**Why:** a claim that survives someone running the command themselves and getting identical
output. `LLM_PROVIDER=none` is pinned for the test child specifically so a browser-started
run is identical to a terminal-started one.

**Cost:** subprocess management, output capping, one-job-at-a-time locking, and a
`_guard()` on every endpoint.

---

## D-23 · `RETRY_LATER` is not a contact

**Alternative:** gate every action with the contact invariants.

**Why:** a silent re-charge of a mandate the customer already authorised does not put a
message in front of a person, so I2, I5 and I7 do not apply — and its annoyance cost is
zero, which is exactly why the policy table reaches for it first wherever the instrument
might still work.

**Cost:** a compliance distinction that must be defended rather than assumed. **I6
deliberately breaks the pattern** and gates retries too: someone who asked to be left alone
was not asking to be left alone noisily.

---

## D-24 · `retry_charge` is always recorded as `simulated`

**Alternative:** dress it up as a live call.

**Why:** test mode exposes no stable API for forcing a subscription charge retry.
**Inventing a "live" record would be a lie in an audit-trail project.**

**Cost:** the headline "executions by mode" table shows more simulated rows than a
less-honest implementation would.

---

## D-25 · The design specification is not published

**Alternative:** ship the pre-build spec alongside the code.

**Why:** it records **intent**, and wherever intent and code disagree, the code and its
tests are the answer. Publishing both invites a reader to check the code against the wrong
artifact.

**Cost:** the source comments carry `docs/NN §M` references to that unpublished spec.
**This folder is the published successor** — where a comment cites `docs/04 §5`, the
corresponding material is now in [07 Guardrails](07-guardrails.md).
