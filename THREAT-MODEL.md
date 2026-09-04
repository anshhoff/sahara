# THREAT-MODEL — what this system cannot do, and why there is no code path for it

A guardrail is a rule that says no. **A non-capability is the absence of the thing that
would have had to say no** — and it is a stronger property, because it cannot be
misconfigured, forgotten in a refactor, or reasoned around by a model.

This document is deliberately about the second kind. Every row names a danger and the
reason the danger has nowhere to happen. Where the answer is a rule rather than an
absence, the row says so, because pretending a flag is a structural property is exactly
the dishonesty this page exists to avoid.

Nothing here is aspirational. Every claim cites the file, and the ones that can be
tested are tested.

---

## 1. The model

The LLM has **no tools, no credentials and no callbacks.** It is called in exactly two
places, both leaf calls that return data, and a third that reads speech.

| Danger | Why there is no path for it |
|---|---|
| The model triggers an action | It cannot. `app/llm.py` and `app/inbound.py` return **values**; every action is dispatched by `policy.decide` from a static table. Nothing in `app/` passes a model output to a function that does anything. `tests/test_llm_boundary.py` greps the package. |
| The model invents a failure category | The enum is closed in **three** independent places: a `Literal` in pydantic, a membership check after it, and a `CHECK` constraint in `schema.sql`. A category outside the six cannot be stored even by a buggy caller. |
| **The model states a wrong amount of money** | **There is no field.** `InboundReading` has `intent`, `promised_date`, `confidence`, `rationale` — and `assert_no_money_field()` runs at **import**, so a money-shaped field makes the module fail to load rather than fail a test. `model_config = {"extra": "forbid"}` rejects a provider that adds one. A caller who says *"tell them I'll pay two hundred rupees"* has nowhere to put the figure. |
| The model writes an amount into outbound copy | **A draft may contain no digit at all.** Numbers arrive only by slot substitution afterwards, so an invented figure is not detected — it is *unrepresentable*. The rule is script-aware: `\d` matches Devanagari ०-९, so a Hindi draft cannot smuggle one either. |
| The model invents a URL | Links are injected by code **after** validation. A draft containing `http://` or `https://` is rejected outright, and the model is never shown a URL. |
| A slow or hostile provider corrupts a decision | Every failure mode — refusal, timeout, rate limit, prose instead of JSON, an invented enum, low confidence, a dead server, no provider at all — collapses to `unknown`, and `unknown` always stops (I4). |
| A model answer is acted on after the world moved | The stale-inference fence: a SHA-256 over decision-relevant fields, taken before the call and rechecked after. A mismatch records the answer and refuses to act on it. |
| Customer PII reaches a provider | The classification prompt carries four Razorpay error fields and nothing else. No name, phone, email or amount. `tests/test_llm_boundary.py` asserts what does and does not cross. |

## 2. Money

| Danger | Why there is no path for it |
|---|---|
| The system takes money from a customer | **There is no charge call anywhere in `app/`.** `retry_charge()` is a simulated re-charge of an existing mandate and creates no API call; the only Razorpay write in the codebase is `payment_link.create`. |
| A Payment Link notifies a real person | Created with `notify: {sms: False, email: False}` and `reminder_enable: False`. Razorpay is structurally unable to contact anyone on our behalf. |
| An amount is wrong | It is never computed. `amount_at_risk_paise` is written once from a signed webhook and frozen; nothing recomputes, projects or multiplies it. Rupees exist only at the moment of printing — the whole system is integer paise. |
| Recovery is claimed without money arriving | A case reaches `recovered` **only** on a `subscription.charged` or `payment_link.paid` event through `intake()`. Sending a link recovers nothing. |
| Runaway spend | `DAILY_OUTREACH_BUDGET_PAISE` is checked against what the *next* contact would cost, so the ceiling is never crossed and then noticed (I7). |
| Uncapped live API calls | `LIVE_LINKS_MAX`, spent **before** the call and never refunded on failure — deliberately fail-closed, so a persistently failing endpoint cannot be retried without bound. |

## 3. The customer

| Danger | Why there is no path for it |
|---|---|
| Someone is contacted after opting out | I3, checked at both gates, re-reading the database each time — an opt-out landing between a decision and its execution still stops it. `tests/test_invariants.py` drives exactly that race. |
| An opt-out on one subscription is ignored on another | I6 is per **person**, in its own table, and applies to silent re-charges too — leaving someone alone does not mean only being quiet at them. |
| Someone is messaged at 2 a.m. | I5, 21:00–09:00 IST. Enforced as a *deferral*, so a contact due at night waits for the morning rather than being dropped. The batch acceptance check re-derives IST from every execution timestamp rather than trusting the gate's own opinion. |
| Someone with two failing subscriptions is messaged twice as often | I7 counts contacts per **customer** across every case. I2 is per case and cannot see the second subscription. |
| A fourth attempt | I1, enforced by a conditional `UPDATE` in `cases.reserve_attempt()` — not by a read, which cannot hold a cap against a concurrent execution. |
| **A real phone call to anyone** | Three independent conditions must all hold, and on synthetic data one of them cannot: the action must be `VOICE_CALL`, `VOICE_REAL_SEND_ENABLED` must be true, and the case must be non-synthetic. **A synthetic customer has no phone number anywhere in this system** — not an empty one, not a placeholder — so there is nothing that could match an allowlist. I8 then requires an explicit E.164 match, and **fails closed on absence**: the allowlist is empty by default, so with no configuration nothing can be dialled. `plivo_trial_verified` reads 0 in every run because it is 0. |
| Dunning someone who already paid | The pre-dispatch fence re-reads the world immediately before any contact. Published with its denominator: *0 of 51 dispatches fenced.* |

## 4. The record

| Danger | Why there is no path for it |
|---|---|
| An audit entry is edited | Every entry is SHA-256'd over its own contents **and its predecessor's hash**, across the whole log. Editing one word invalidates every hash after it, and `verify()` names the first break. |
| An inconvenient case is deleted whole | The chain runs across the entire log rather than per case, precisely because a whole case is the more attractive thing to delete — it removes a number from the metrics as well as its explanation. |
| An entry is quietly removed | `(case_id, seq)` is UNIQUE and allocated as `MAX(seq)+1` inside the insert's transaction, so trails are gapless from 1. An acceptance check asserts it on every case. |
| An UPDATE or DELETE appears against the log | There is none in `app/`, and `tests/test_llm_boundary.py` greps for one and fails the build if it ever appears. |
| Unverifiable rows pass as verified | Rows written before the chain existed are counted as `n_unchained` and reported separately, never folded into a passing result. |
| A headline number drifts from the code | `scripts/verify_numbers.py --check` re-derives all 118 published values from the seed and byte-compares. CI is red until somebody runs `--update` and explains the change. |

## 5. The deployment

| Danger | Why there is no path for it |
|---|---|
| A public demo is made to move real money | `PUBLIC_DEMO=true` **refuses to boot** if any provider credential is present — `assert_demo_safe()` runs before the database is even opened, and the process exits non-zero. A demo that could be handed a credential is not a demo. |
| A write route is found on a public demo | Every non-GET returns **404, not 403**. A 403 announces there is an endpoint here; a 404 says there is nothing — which in demo mode is true, because the mutating routers are not mounted. |
| The control room runs subprocesses on a public host | It is a separate router, mounted only when `CONTROL_API_ENABLED` is true, and `demo_violations()` treats it being on as a reason to refuse to boot. |
| Any web page drives the unauthenticated API | CORS is an explicit origin list, never `*`. |
| An unsigned webhook creates a row | Signature verification returns 400 and touches nothing. **No row of any kind** exists for an unverified body. |
| A replayed webhook double-charges an attempt | `razorpay_event_id` is UNIQUE and the insert **is** the dedupe claim — the event row is written before the case, so a lost race leaves no empty case behind. |

## 6. What this does NOT protect against

Stating these is the point of the page. A threat model with no honest gaps is marketing.

| Not covered | Why |
|---|---|
| **Authentication of any kind** | The API is unauthenticated by design on a single-operator local app. Anyone who can reach the port can read every case. This is why the public demo mode disables writes entirely rather than authenticating them. |
| **Someone with write access to the database file** | They can rewrite anything. What they cannot do is rewrite it *undetectably* — the hash chain turns silent tampering into visible tampering. That is the whole claim, and it is smaller than "the data is safe". |
| **DLT registration and telecom compliance** | Never in scope. A real dunning call in India needs registered headers and templates this project does not have, which is why I8's boundary is structural rather than a caveat. |
| **The correctness of the assumptions** | Every outcome probability, cost and prior is a stated estimate. No guardrail makes a wrong assumption right; see `VERIFY.md`. |
| **Multi-tenancy** | There is none. One database, one merchant, no isolation between anything. |
| **A compromised dependency** | Pinned versions in `requirements.txt` and nothing else. There is no supply-chain verification here and it would be dishonest to imply one. |
| **Rate limiting, DoS, or abuse of the read API** | Absent. The demo is expected to sit behind whatever hosts it. |

---

## The shape of the argument

Almost every row above is either **an absent code path** or **an invariant enforced in a
module that imports neither the policy table nor the model.** The two exceptions —
`PUBLIC_DEMO` and `VOICE_REAL_SEND_ENABLED` — are flags, and both are written to fail
closed: the demo refuses to start rather than starting permissively, and voice needs
three independent conditions to line up before a signal can leave the process.

The strongest security property of this system is the list of things it has no way to do.
