#!/usr/bin/env python3
"""Synthetic case generator (docs/05).

Emits Razorpay-webhook-shaped payloads — not application rows. The generator does
not know the database schema; it only knows the payload shape, so generated cases
enter through exactly the same door as live deliveries.

Everything is driven by a single seeded random.Random, so the same --seed produces a
byte-identical file and therefore reproducible demo numbers.

    python scripts/generate_synthetic.py --n 80 --seed 42 --out synthetic_cases.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Batch start: fixed so that timestamps, and therefore the whole run, are reproducible.
BATCH_START = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)
WINDOW_DAYS = 7  # initial failures are spread across the 7 days before batch start

# docs/05 §3
CATEGORY_WEIGHTS = {
    "insufficient_funds": 35,
    "card_expired": 25,
    "issuer_declined": 15,
    "authentication_failed": 10,
    "invalid_payment_method": 8,
    "unknown": 7,
}
AMOUNTS_PAISE = [19900, 49900, 99900, 199900, 499900]
AMOUNT_WEIGHTS = [30, 35, 20, 10, 5]

# Error text per category. Chosen to feed rules R1–R6 the way real payloads do.
ERROR_FLAVOURS = {
    "insufficient_funds": [
        ("BAD_REQUEST_ERROR", "payment_failed", "Your card has insufficient funds to complete this payment", "bank"),
        ("BAD_REQUEST_ERROR", "payment_failed", "Payment failed because the account has insufficient balance", "bank"),
        ("BAD_REQUEST_ERROR", "payment_failed", "Transaction amount exceeds limit set on the card", "bank"),
    ],
    "card_expired": [
        ("BAD_REQUEST_ERROR", "card_expired", "The card used for this payment has expired", "customer"),
        ("BAD_REQUEST_ERROR", "card_expired", "Payment failed: expired card. Please use another card", "customer"),
    ],
    "issuer_declined": [
        ("BAD_REQUEST_ERROR", "payment_failed", "Transaction declined by the issuing bank (do_not_honour)", "bank"),
        ("GATEWAY_ERROR", "payment_failed", "Payment timed out at the bank's end", "gateway"),
        ("BAD_REQUEST_ERROR", "payment_failed", "The transaction was declined by your bank", "bank"),
    ],
    "authentication_failed": [
        ("BAD_REQUEST_ERROR", "payment_failed", "3DSecure authentication failed for this transaction", "customer"),
        ("BAD_REQUEST_ERROR", "payment_failed", "OTP entry was not completed in time", "customer"),
    ],
    "invalid_payment_method": [
        ("BAD_REQUEST_ERROR", "payment_failed", "Card blocked by the issuing bank", "bank"),
        ("BAD_REQUEST_ERROR", "payment_failed", "The mandate cancelled by the customer cannot be charged", "customer"),
    ],
}

# docs/05 §3 (b): odd-but-classifiable strings that deliberately miss every rule, so
# the LLM fallback is actually exercised in the batch instead of being decorative.
LLM_FALLBACK_FLAVOURS = [
    (("BAD_REQUEST_ERROR", "payment_failed",
      "The customer's bank did not permit this standing instruction at this time", "bank"),
     "issuer_declined"),
    (("BAD_REQUEST_ERROR", "payment_failed",
      "Recurring debit rejected: cardholder record is no longer active with the issuer", "bank"),
     "invalid_payment_method"),
    (("BAD_REQUEST_ERROR", "payment_failed",
      "Bank returned a negative response for the standing instruction", "bank"),
     "issuer_declined"),
]


def weighted_choice(rng: random.Random, options: list, weights: list[int]):
    return rng.choices(options, weights=weights, k=1)[0]


def payment_failed_payload(*, event_id: str, sub_id: str, cust_id: str, pay_id: str,
                           amount_paise: int, error: tuple[str, str, str, str] | None,
                           occurred: datetime, opted_out: bool = False,
                           event_type: str = "payment.failed") -> dict:
    """One Razorpay-shaped event body. `error=None` reproduces the malformed case:
    a payload with no error information at all."""
    payment_entity = {
        "id": pay_id,
        "entity": "payment",
        "amount": amount_paise,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "customer_id": cust_id,
        "invoice_id": f"SYNTH-inv-{pay_id.split('-')[-1]}",
        "international": False,
        "captured": False,
        "created_at": int(occurred.timestamp()),
        "notes": {"subscription_id": sub_id, "customer_id": cust_id},
    }
    if error is not None:
        code, reason, description, source = error
        payment_entity.update({
            "error_code": code,
            "error_description": description,
            "error_source": source,
            "error_step": "payment_authorization",
            "error_reason": reason,
        })

    return {
        "entity": "event",
        "account_id": "acc_SYNTHETIC",
        "event": event_type,
        "contains": ["payment", "subscription"],
        "payload": {
            "payment": {"entity": payment_entity},
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "entity": "subscription",
                    "plan_id": "SYNTH-plan-demo-monthly-499",
                    "customer_id": cust_id,
                    "status": "pending" if event_type == "subscription.pending" else "active",
                    "total_count": 12,
                }
            },
        },
        "created_at": int(occurred.timestamp()),
        "id": event_id,
        # docs/05 §5: synthetic labelling is non-negotiable and starts at the payload.
        "synthetic": True,
        "customer_opted_out": opted_out,
    }


def make_case(rng: random.Random, index: int, category: str, *, error=None,
              category_truth: str | None = None, opted_out: bool = False,
              amount_paise: int | None = None, occurred: datetime | None = None,
              outcome_script: str = "roll", **profile_extra) -> dict:
    ref = f"SYNTH-C-{index:04d}"
    sub_id = f"SYNTH-sub-{index:04d}"
    cust_id = f"SYNTH-cust-{index:04d}"
    pay_id = f"SYNTH-pay-{index:04d}"
    event_id = f"SYNTH-evt-{index:04d}-1"
    amount = amount_paise if amount_paise is not None else weighted_choice(rng, AMOUNTS_PAISE, AMOUNT_WEIGHTS)
    when = occurred or (BATCH_START - timedelta(
        hours=rng.randint(1, WINDOW_DAYS * 24), minutes=rng.randint(0, 59)))

    if error is None and category in ERROR_FLAVOURS:
        error = rng.choice(ERROR_FLAVOURS[category])

    profile = {
        "category_truth": category_truth or category,
        "amount_paise": amount,
        "opted_out": opted_out,
        "outcome_script": outcome_script,
        # Ground truth is for evaluation only. The pipeline never reads it — the
        # runner only uses it to report classification accuracy.
        "expects_llm_fallback": bool(profile_extra.pop("expects_llm_fallback", False)),
    }
    profile.update(profile_extra)

    return {
        "synthetic_case_ref": ref,
        "initial_event": payment_failed_payload(
            event_id=event_id, sub_id=sub_id, cust_id=cust_id, pay_id=pay_id,
            amount_paise=amount, error=error, occurred=when, opted_out=opted_out,
        ),
        "profile": profile,
    }


def edge_cases(rng: random.Random) -> list[dict]:
    """One per stopping rule, plus the systemic nasties (docs/05 §4). Always appended
    on top of the sampled n, with fixed ids, because these are the graceful-failure
    demo material."""
    out: list[dict] = []
    base = BATCH_START - timedelta(hours=6)

    def edge(n: int, **kw):
        c = make_case(rng, 9000 + n, kw.pop("category"), occurred=base, **kw)
        c["synthetic_case_ref"] = f"SYNTH-E-{n:02d}"
        return c

    # E-01 — opted out from the start: I3 at the pre-decision gate, zero contacts.
    out.append(edge(1, category="insufficient_funds", opted_out=True, outcome_script="never",
                    edge_expectation="stopped_opt_out, attempt_count=0"))

    # E-02 — opts out between attempt 1 and attempt 2: I3 at the pre-execution gate.
    out.append(edge(2, category="insufficient_funds", outcome_script="never",
                    opt_out_after_attempt=1,
                    edge_expectation="stopped_opt_out, attempt_count=1"))

    # E-03 — never recovers: exactly 3 attempts, then the promise lapses. I1.
    out.append(edge(3, category="insufficient_funds", outcome_script="never",
                    edge_expectation="stopped_handoff via lapsed promise, attempt_count=3"))

    # E-04 — a second failure 2h after attempt 1's contact: I2 must defer attempt 2.
    out.append(edge(4, category="card_expired", outcome_script="recover_on_attempt_2",
                    followup_delay_hours=2,
                    edge_expectation="visible I2 deferral before attempt 2"))

    # E-05 — garbage error fields: no rule matches, the model has nothing solid,
    # confidence collapses to unknown. I4.
    out.append(edge(5, category="unknown",
                    error=("ERR_XX_99", "ERR_XX_99",
                           "lorem ipsum dolor sit amet consectetur adipiscing elit", "unknown"),
                    category_truth="unknown", outcome_script="never",
                    edge_expectation="stopped_unknown, attempt_count=0"))

    # E-06 — the exact same event id delivered twice: dedupe must absorb it.
    e6 = edge(6, category="card_expired", outcome_script="recover_on_attempt_1",
              duplicate_delivery=True,
              edge_expectation="one case, one failure_event despite two deliveries")
    out.append(e6)

    # E-07 — malformed payload with no error object at all: R7, no crash.
    out.append(edge(7, category="unknown", error=None, category_truth="unknown",
                    outcome_script="never",
                    edge_expectation="stopped_unknown via R7, receiver does not crash"))

    # E-08 — recovers via the link only, never via a retry.
    out.append(edge(8, category="authentication_failed", outcome_script="recover_via_link",
                    edge_expectation="recovered, attributed to a SEND_UPDATE_LINK execution"))
    return out


def generate(n: int, seed: int) -> dict:
    rng = random.Random(seed)
    categories = list(CATEGORY_WEIGHTS)
    weights = [CATEGORY_WEIGHTS[c] for c in categories]

    # Exact counts rather than per-case rolls, so the distribution matches the table
    # even at n=60 and does not wander with the seed.
    counts = {c: round(n * CATEGORY_WEIGHTS[c] / 100) for c in categories}
    drift = n - sum(counts.values())
    counts["insufficient_funds"] += drift

    cases: list[dict] = []
    index = 1
    for category, count in counts.items():
        for i in range(max(0, count)):
            if category == "unknown":
                # Half the unknown bucket is genuinely empty (R7, no model involved);
                # half is odd-but-classifiable text that misses every rule and so
                # actually exercises the LLM fallback.
                if i % 2 == 0:
                    cases.append(make_case(rng, index, "unknown", error=None,
                                           category_truth="unknown", outcome_script="never"))
                else:
                    flavour, truth = LLM_FALLBACK_FLAVOURS[(i // 2) % len(LLM_FALLBACK_FLAVOURS)]
                    cases.append(make_case(rng, index, "unknown", error=flavour,
                                           category_truth=truth, expects_llm_fallback=True))
            else:
                cases.append(make_case(rng, index, category))
            index += 1

    # ~10 hand-picked scripted outcomes so the headline numbers are not pure RNG and
    # the time-to-recovery chart is not degenerate. Labelled as assumptions, not data.
    scripted = rng.sample(range(len(cases)), min(10, len(cases)))
    scripts = ["recover_on_attempt_1", "recover_on_attempt_2", "recover_on_attempt_2",
               "never", "recover_on_attempt_1"]
    for k, idx in enumerate(scripted):
        if cases[idx]["profile"]["category_truth"] == "unknown":
            continue
        cases[idx]["profile"]["outcome_script"] = scripts[k % len(scripts)]

    cases.extend(edge_cases(rng))

    return {
        "meta": {
            "seed": seed,
            "n_sampled": n,
            "n_edge_cases": 8,
            "n_total": len(cases),
            "batch_start": BATCH_START.isoformat().replace("+00:00", "Z"),
            "synthetic": True,
            "note": ("All cases are synthetic. Outcome probabilities in run_batch.py are "
                     "modelling assumptions, not measured industry data."),
        },
        "cases": cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic failed-subscription cases")
    ap.add_argument("--n", type=int, default=80,
                    help="sampled cases, at least 60 (edge cases are added on top). The upper "
                         "bound was 100 when this only fed a demo batch; the measurement runs "
                         "need thousands of cases for the confidence intervals to mean anything, "
                         "and nothing in the generator is bounded by scale.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="synthetic_cases.json")
    args = ap.parse_args()

    # The floor stays: below ~60 the exact-count category split stops matching the
    # weights table. The ceiling of 100 was a demo-scale assumption, and it is gone —
    # every confidence interval this project publishes gets narrower with n, and
    # nothing in the generator is bounded by scale.
    if args.n < 60:
        ap.error("--n must be at least 60 (docs/05 §1)")

    data = generate(args.n, args.seed)
    Path(args.out).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    dist: dict[str, int] = {}
    for c in data["cases"]:
        dist[c["profile"]["category_truth"]] = dist.get(c["profile"]["category_truth"], 0) + 1
    print(f"wrote {args.out}: {data['meta']['n_total']} cases "
          f"({args.n} sampled + {data['meta']['n_edge_cases']} edge), seed {args.seed}")
    for k in sorted(dist):
        print(f"  {k:24s} {dist[k]:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
