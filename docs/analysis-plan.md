# Analysis plan — what counts as a result

**Written after the first runs, not before it.** This document is not a
pre-registration and must not be read as one. Pre-registration means committing to a
hypothesis and an analysis before seeing any data, and the first batches of this
project had already been run when this file was created. Claiming otherwise
retroactively would be the single most dishonest thing in the repository.

What this file *does* do is fix the hierarchy from here on, so that "which number is
the headline?" cannot be re-decided after a run comes back looking bad. That is the
defensible half of pre-registration, and it is worth having on its own.

Related: [11 · Measurement](11-measurement.md) for how each number is computed,
[19 · Decision log](19-decision-log.md) for why.

---

## 1. The one primary metric

> **Net incremental recovery** — money that came back **because of** the agent
> (treated arm minus control arm), minus **everything** the agent spent getting it,
> with a 95% percentile-bootstrap confidence interval.
>
> `metrics.net_recovery()["net_incremental_paise"]` and `…_ci95`.

A result is claimed **only** when that interval **excludes zero**. If it does not, the
correct sentence is *"we could not measure an effect at this sample size"* — not a
point estimate quoted without its interval, and not a switch to whichever secondary
metric happened to clear.

**Why this one.** Three properties, and no other candidate has all three:

1. **It can go down.** Gross recovery cannot decrease by sending more messages, which
   is precisely what makes it useless as an optimisation target: on a gross ledger the
   marginal message is free. It is not free.
2. **It is causal.** The control arm is subtracted, so recovery that would have
   happened anyway is not credited to the agent.
3. **It is net.** Every rupee of outreach and every ₹40 of human-queue time is
   subtracted, including on cases that did not recover.

## 2. The one secondary metric

> **Lift in recovery rate**, treated minus control, in percentage points, with a 95%
> CI. `metrics.incremental_recovery()["lift"]` and `["lift_ci95"]`.

Reported **always and alongside** the primary, never instead of it. The rate lift is
the more stable of the two — ticket sizes span ₹199 to ₹4,999, so the money estimate
needs a far larger n before it settles — which is exactly why it is the secondary and
not the headline. **Quoting the rate lift while the money interval spans zero is the
easy dishonesty here, and it is forbidden.**

## 3. Everything else is descriptive

Reported, traceable, and **never** promoted to a headline, whichever way it lands:

per-category lift · recovery rate (gross, and strict) · cost per ₹100 recovered ·
average time to recovery · classification precision / recall / F1 · prior calibration
(Brier, ECE) · stage latency p50/p95 · execution modes · suppression and stop counts ·
organic recovery per arm · self-cure roll balance.

A descriptive number may **support** the primary claim. It may not **replace** it.

## 4. Fixed before the next run

| Decision | Value | Why it is fixed |
|---|---|---|
| Primary metric | net incremental recovery | §1 |
| Success criterion | 95% CI excludes zero | not "the point estimate is positive" |
| Interval method | percentile bootstrap, 10,000 resamples, seed 42 | deterministic and re-derivable |
| Analysis population | **intention-to-treat** — every case counts in the arm it was assigned to, whatever status it reached | dropping treated cases the agent refused to act on would flatter the result by exactly the cases it handled most conservatively |
| Arm assignment | decided at generation, carried on the event, never re-rolled inside the pipeline | `tests/test_holdout.py` |
| Holdout fraction | 0.35 | |
| Seed | 42 | |
| Multiplicity | per-category lifts are **descriptive**; no per-category result is claimed as a finding | §3, and no correction is applied because no claim is made |

## 5. What we will publish even when it is unflattering

* **Categories where the agent is flat or negative.** Publishing where it does nothing
  is what makes the rest believable.
* **The LLM ablation, in whichever direction it lands.** If the keyword baseline beats
  the model at promise extraction, that is the published number. A measured negative,
  honestly kept, is worth more than a flattering one.
* **Prior calibration.** `P_RECOVER_PRIOR` drives every EV gate and has never been
  scored against reality. Wherever the priors are wrong, the table says so.
* **Every superseded number.** When a measurement fix moves the headline, the old
  output file stays in the repository unedited and the diff is recorded with its
  cause — rather than quietly overwritten.

## 6. What this plan does not cover

The interval is **sampling uncertainty only**. It quantifies how much of the gap could
be chance at this sample size. It says nothing about whether the underlying outcome
model is right, and on a synthetic batch that model is a stated assumption
(`SUCCESS_PROBABILITY`, `BASELINE_RECOVERY_PROBABILITY`, `ACTION_COST_PAISE`,
`P_RECOVER_PRIOR`). **What is measured here is the mechanism, not the market.**
