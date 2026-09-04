# Documentation

Complete technical documentation for the **Failed Subscription Recovery Agent**
(Razorpay AI Buildathon — Track 03: AI Revenue Recovery).

The root [`README.md`](../README.md) is the *operator's* guide: install it, run it,
verify its numbers. This folder is the *engineer's* guide: what every module does, why
it is shaped that way, and what would break if it were shaped differently.

Where the two disagree, **the code and its tests are the answer** — every claim below
names the file that enforces it.

---

## Reading paths

| If you are… | Read, in this order |
|---|---|
| **Evaluating the project** (judge / reviewer) | [01 Overview](01-overview.md) → [07 Guardrails](07-guardrails.md) → [09 LLM boundary](09-llm-boundary.md) → [11 Measurement](11-measurement.md) → [18 Limits](18-limits-and-roadmap.md) |
| **Reading the code for the first time** | [02 Architecture](02-architecture.md) → [03 Data model](03-data-model.md) → [04 Pipeline](04-pipeline.md) → [15 Testing](15-testing.md) |
| **Running or operating it** | [16 Operations](16-operations.md) → [12 API reference](12-api-reference.md) → [13 Dashboard](13-dashboard.md) |
| **Changing behaviour** | [05 Diagnosis](05-diagnosis.md) · [06 Policy](06-policy.md) · [07 Guardrails](07-guardrails.md) · [08 Economics](08-economics.md) → [19 Decision log](19-decision-log.md) |
| **Reproducing the numbers** | [14 Batch & synthetic data](14-batch-and-synthetic.md) → [11 Measurement](11-measurement.md) |

---

## Contents

### Foundations
| # | Document | What it answers |
|---|---|---|
| 01 | [Overview](01-overview.md) | What the system is, the results it claims, and the vocabulary the rest of the docs use |
| 02 | [Architecture](02-architecture.md) | Processes, modules, dependency rules, the two clocks, the two run modes |
| 03 | [Data model](03-data-model.md) | Eight tables, the case state machine, ID conventions, CHECK constraints as enum backstops |

### The recovery loop
| # | Document | What it answers |
|---|---|---|
| 04 | [Pipeline](04-pipeline.md) | Detect → Diagnose → Decide → Execute → Outcome, end to end, with sequence diagrams |
| 05 | [Diagnosis](05-diagnosis.md) | Rule table R1–R7, when the model is consulted, how a verdict is recorded |
| 06 | [Decision policy](06-policy.md) | The 6×3 static policy table, why each cell is what it is |
| 07 | [Guardrails](07-guardrails.md) | Invariants I1–I7 and gate E1 — what the agent refuses to do, and where each gate runs |
| 08 | [Economics](08-economics.md) | Pricing an intervention before taking it: EV, costs, priors, and the cost ledger |
| 09 | [LLM boundary](09-llm-boundary.md) | Exactly where the model is, what it can and cannot reach, and every failure mode |

### Evidence
| # | Document | What it answers |
|---|---|---|
| 10 | [Audit trail](10-audit-trail.md) | The hash-chained, append-only log and how to verify it as a sceptic |
| 11 | [Measurement](11-measurement.md) | Every metric's definition, traceability, and the randomised control arm |
| 14 | [Batch & synthetic data](14-batch-and-synthetic.md) | The generator, the outcome model, the runner, the acceptance checks |
| 15 | [Testing](15-testing.md) | All 133 tests, what each file pins, and the adversarial suite |

### Surfaces
| # | Document | What it answers |
|---|---|---|
| 12 | [API reference](12-api-reference.md) | Every HTTP endpoint, its shape and its contract |
| 13 | [Dashboard](13-dashboard.md) | The nine console views and the control room |
| 16 | [Operations](16-operations.md) | Setup, environment variables, LLM modes, live mode, runbook, troubleshooting |

### Depth
| # | Document | What it answers |
|---|---|---|
| 17 | [Concurrency & failure](17-concurrency-and-failure.md) | Every race the system is exposed to, the claim tokens, at-most-once posture |
| 18 | [Limits & roadmap](18-limits-and-roadmap.md) | What is not proven, what is out of scope, and what extending it would take |
| 19 | [Decision log](19-decision-log.md) | The consequential design decisions, each with its alternative and its cost |
| 20 | [Voice, promises & the AI question](20-voice-and-promises.md) | Escalation rung 3, the inbound schema with no amount field, I8, and the measured LLM ablation |
| — | [Analysis plan](analysis-plan.md) | One primary metric, one secondary, everything else descriptive |
| — | [Glossary](glossary.md) | Every term of art in one place |

---

## Diagram conventions

All diagrams are [Mermaid](https://mermaid.js.org) and render natively on GitHub.

| Shape / colour | Means |
|---|---|
| Rectangle | A module, function or process |
| Rounded / stadium | An external system (Razorpay, the model provider) |
| Diamond | A gate — something that can stop or defer |
| Cylinder | A database table |
| Dashed arrow | An optional, best-effort, or read-only path |
| `🔴 stop` / `🟡 defer` / `🟢 pass` | A gate verdict |

---

## Documentation invariants

These rules keep this folder from drifting away from the code:

1. **Every rule cites its enforcing file.** If a doc states a bound, it names the
   module and the test that holds it.
2. **Numbers are quoted with their run.** Any figure comes from the frozen 88-case
   batch (`seed 42`, `LLM_PROVIDER=none`) unless labelled otherwise.
3. **Assumptions are labelled as assumptions.** Cost constants, recovery priors and
   the batch outcome model are stated estimates, never presented as measurement.
4. **The code wins.** These documents describe intent; `app/` and `tests/` are the
   specification of record.
