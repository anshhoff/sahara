#!/usr/bin/env python3
"""LLM ablation: rules versus model at reading an inbound utterance (task 3.8).

Everywhere else in this project the LLM is bounded, gated and fallen back from, and
the honest summary of its contribution is "the rules do almost all of it". That is a
claim about the DIAGNOSIS path, where the input is four structured Razorpay error
fields and keyword rules are genuinely excellent.

This is the other path. The input here is a sentence a person said on the phone, in
Hinglish, and the output is a dated commitment. It is the one job in this system that
rules are actually bad at, which makes it the one place where a model either earns its
place or does not — and it is the only place the answer can be measured rather than
asserted.

    python scripts/ablate_inbound.py [--out docs/ablation-inbound.json]

Three columns are scored, and only the third decides anything:

  intent          did the reader pick the right one of eight?
  date            did it recover the right calendar date?
  policy facts    did it produce the same (action, date) the system would act on?

The third is the column that matters. Two readings that disagree about wording but
schedule the same sweep on the same day have not disagreed about anything the system
does. Significance is McNemar's exact test on the paired disagreements, because both
readers see the identical items and an unpaired test would throw that away.

THE RESULT IS PUBLISHED IN WHICHEVER DIRECTION IT LANDS. If the keyword baseline wins,
that is the number that goes in the README. A measured negative, honestly kept, is
worth more than a flattering one — and this file is written so that outcome costs
nothing to report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock, config, inbound, llm  # noqa: E402

# The clock every relative date is resolved against. Fixed, so "Friday" means the same
# day on every run and the labels below stay correct.
T0 = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)   # a Monday

# ---------------------------------------------------------------- the labelled set
# Hand-labelled by the author, and deliberately NOT drawn only from the batch's own
# transcript pool — a test set copied from the training set measures nothing. Roughly a
# third are phrasings the keyword table was never written against, which is the honest
# way to ask whether keywords generalise.
#
# `date` is an ISO date or None, resolved against T0 (Monday 2 March 2026).
CASES: tuple[tuple[str, str, Optional[str]], ...] = (
    # --- phrasings the rule table WAS written for
    ("haan bhai, salary aane ke baad Friday ko kar dunga", "will_pay_on_date", "2026-03-06"),
    ("abhi paise nahi hain, agle Monday tak kar deta hun", "will_pay_on_date", "2026-03-09"),
    ("kal kar dunga, thoda busy hoon abhi", "will_pay_on_date", "2026-03-03"),
    ("parso pakka pay kar dunga, promise", "will_pay_on_date", "2026-03-04"),
    ("I will pay by 2026-03-14, please do not call again before that",
     "will_pay_on_date", "2026-03-14"),
    ("abhi hi kar raha hoon, link bhej do", "will_pay_now", None),
    ("paying right now on the app", "will_pay_now", None),
    ("sorry yaar, abhi nahi kar sakta, paise nahi hain", "cannot_pay", None),
    ("I cannot pay this month at all", "cannot_pay", None),
    ("maine to already payment kar diya tha, aapke system mein galti hai",
     "disputes_charge", None),
    ("why am I being charged? I cancelled this subscription", "disputes_charge", None),
    ("galat number hai bhai, main koi subscription nahi leta", "wrong_number", None),
    ("wrong number, this is not my account", "wrong_number", None),
    ("mat karo call baar baar, band karo ye sab", "opt_out", None),
    ("do not call me again, remove my number", "opt_out", None),
    ("[no answer]", "no_answer", None),

    # --- phrasings it was NOT written for
    ("thoda time do, shanivar tak dekhta hoon", "will_pay_on_date", "2026-03-07"),
    ("guruvar ko pension aati hai, us din settle kar dunga", "will_pay_on_date", "2026-03-05"),
    ("EMI ke baad hi kuch ho payega, is hafte nahi", "cannot_pay", None),
    ("mera account to band ho chuka hai kab ka", "disputes_charge", None),
    ("bhaiya main to driver hoon, sahab ka phone hai ye", "wrong_number", None),
    ("dobara phone mat karna, samajh aaya?", "opt_out", None),
    ("dekhta hoon, abhi kuch keh nahi sakta", "unclear", None),
    ("hmm... theek hai", "unclear", None),
    ("arre wo card to maine block karwa diya tha, naya bhejo",
     "unclear", None),
    ("kal tak nahi ho payega, parso tak dekhta hoon", "will_pay_on_date", "2026-03-04"),
    ("tuesday morning first thing, pakka", "will_pay_on_date", "2026-03-03"),
    ("main abhi office mein hoon, baad mein baat karte hain", "unclear", None),
    ("paise hain hi nahi, kahan se dun", "cannot_pay", None),
    ("aap log fraud ho, main consumer court jaunga", "disputes_charge", None),
)


def _resolve(text: Optional[str]) -> Optional[str]:
    return inbound.resolve_date(text, now=T0)


def score(reading: dict[str, Any], truth_intent: str, truth_date: Optional[str]) -> dict[str, bool]:
    truth_facts = inbound.policy_facts({"intent": truth_intent, "promised_date": truth_date})
    return {
        "intent": reading.get("intent") == truth_intent,
        "date": reading.get("promised_date") == truth_date,
        "policy_facts": inbound.policy_facts(reading) == truth_facts,
    }


# ------------------------------------------------------------------ McNemar
def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact p for the paired disagreements.

    `b` = items the model got right and the rules got wrong, `c` = the reverse. The
    agreements carry no information about which reader is better, which is exactly why
    they are excluded — and why a paired test is the correct one here: both readers see
    the identical items, and an unpaired comparison would throw that pairing away.

    Under the null the two readers are equally likely to be the one that is right, so
    b ~ Binomial(b + c, 0.5). Exact rather than the chi-square approximation because
    thirty items produce single-digit discordant counts, where the approximation is
    not trustworthy.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval — correct near 0 and 1, where the normal interval is not."""
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def run(limit: Optional[int] = None, pause: float = 0.4) -> dict[str, Any]:
    clock.set_clock(clock.SimulatedClock(T0))
    items = CASES[:limit] if limit else CASES

    rows: list[dict[str, Any]] = []
    for transcript, truth_intent, truth_date in items:
        rule = inbound.read_by_rules(transcript, now=T0)
        if llm.enabled():
            model = inbound.read_by_model(transcript, now=T0)
            time.sleep(pause)                     # free tiers rate-limit
        else:
            model = {"intent": "unclear", "promised_date": None, "confidence": 0.0,
                     "rationale": "no model configured"}
        rows.append({
            "transcript": transcript,
            "truth": {"intent": truth_intent, "promised_date": truth_date},
            "rule": rule,
            "model": model,
            "rule_score": score(rule, truth_intent, truth_date),
            "model_score": score(model, truth_intent, truth_date),
        })

    n = len(rows)
    out_columns: dict[str, Any] = {}
    for column in ("intent", "date", "policy_facts"):
        rule_ok = sum(1 for r in rows if r["rule_score"][column])
        model_ok = sum(1 for r in rows if r["model_score"][column])
        b = sum(1 for r in rows if r["model_score"][column] and not r["rule_score"][column])
        c = sum(1 for r in rows if r["rule_score"][column] and not r["model_score"][column])
        p = mcnemar_exact(b, c)
        out_columns[column] = {
            "n": n,
            "rule_correct": rule_ok,
            "model_correct": model_ok,
            "rule_accuracy": round(rule_ok / n, 4) if n else None,
            "model_accuracy": round(model_ok / n, 4) if n else None,
            "rule_accuracy_ci95": wilson(rule_ok, n),
            "model_accuracy_ci95": wilson(model_ok, n),
            "model_wins": b,
            "rule_wins": c,
            "mcnemar_p": round(p, 5),
            "significant_at_05": p < 0.05,
            "winner": ("model" if model_ok > rule_ok else
                       "rule" if rule_ok > model_ok else "tie"),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved_against": T0.isoformat(),
        "n_items": n,
        "model": config.LLM_MODEL if llm.enabled() else None,
        "provider": config.LLM_PROVIDER,
        "columns": out_columns,
        "rows": rows,
        "method": {
            "test": "McNemar exact, two-sided, on the paired disagreements",
            "why_paired": ("both readers see the identical items; the agreements carry no "
                           "information about which reader is better, and an unpaired test "
                           "would discard the pairing"),
            "why_exact": ("thirty items produce single-digit discordant counts, where the "
                          "chi-square approximation is not trustworthy"),
            "primary_column": "policy_facts",
            "why": ("two readings that disagree about wording but schedule the same sweep on "
                    "the same day have not disagreed about anything the system does"),
        },
        "honesty": ("labels are the author's, on thirty items — small, and the interval says "
                    "so. Roughly a third of the items are phrasings the keyword table was "
                    "never written against, which is the honest way to ask whether keywords "
                    "generalise. The result is published in whichever direction it lands."),
    }


def print_report(result: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print("INBOUND READING — RULES vs MODEL")
    print("=" * 78)
    print(f"  items      {result['n_items']}   model {result['model'] or '(none configured)'}")
    print(f"  clock      {result['resolved_against']}")
    print()
    print(f"  {'column':14s} {'rules':>18s} {'model':>18s} {'b/c':>9s} {'p':>8s}  winner")
    print("  " + "-" * 74)
    for column, s in result["columns"].items():
        rule = (f"{s['rule_accuracy'] * 100:5.1f}% [{s['rule_accuracy_ci95'][0] * 100:.0f},"
                f"{s['rule_accuracy_ci95'][1] * 100:.0f}]")
        model = (f"{s['model_accuracy'] * 100:5.1f}% [{s['model_accuracy_ci95'][0] * 100:.0f},"
                 f"{s['model_accuracy_ci95'][1] * 100:.0f}]")
        star = "*" if s["significant_at_05"] else " "
        print(f"  {column:14s} {rule:>18s} {model:>18s} "
              f"{s['model_wins']:>4d}/{s['rule_wins']:<4d} {s['mcnemar_p']:>8.4f}{star} "
              f"{s['winner']}")
    print()
    print("  b = model right & rules wrong, c = the reverse. * = p < 0.05.")
    print("  The column that decides anything is `policy_facts`: whether the reading")
    print("  produces the same scheduling decision the system would act on.")

    primary = result["columns"]["policy_facts"]
    print("\n  " + "-" * 74)
    if primary["winner"] == "tie":
        verdict = ("The model and a keyword table are indistinguishable at this sample "
                   "size on the column that matters.")
    elif primary["significant_at_05"]:
        verdict = (f"The {primary['winner']} reader wins on policy facts, "
                   f"p = {primary['mcnemar_p']:.4f}.")
    else:
        verdict = (f"The {primary['winner']} reader leads on policy facts but the "
                   f"difference is not significant at this sample size "
                   f"(p = {primary['mcnemar_p']:.4f}).")
    print(f"  VERDICT: {verdict}")
    print("  Published as measured, in whichever direction it landed.")

    disagreements = [r for r in result["rows"]
                     if r["rule_score"]["policy_facts"] != r["model_score"]["policy_facts"]]
    if disagreements:
        print(f"\n  the {len(disagreements)} item(s) the two readers decided differently:")
        for r in disagreements:
            winner = "model" if r["model_score"]["policy_facts"] else "rule"
            print(f"    [{winner:5s}] {r['transcript'][:58]!r}")
            print(f"             truth={r['truth']['intent']}/{r['truth']['promised_date']}  "
                  f"rule={r['rule']['intent']}/{r['rule']['promised_date']}  "
                  f"model={r['model']['intent']}/{r['model']['promised_date']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/ablation-inbound.json")
    ap.add_argument("--limit", type=int, default=None, help="score only the first N items")
    ap.add_argument("--pause", type=float, default=0.4,
                    help="seconds between model calls (free tiers rate-limit)")
    args = ap.parse_args()

    problem = llm.misconfiguration()
    if problem:
        print(f"WARNING: {problem}")
    if not llm.enabled():
        print("WARNING: LLM_PROVIDER=none. The model column will be all `unclear`, which is\n"
              "         a correct reading of an absent model and a useless ablation. Set a\n"
              "         provider to measure anything.\n")

    result = run(limit=args.limit, pause=args.pause)
    print_report(result)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
