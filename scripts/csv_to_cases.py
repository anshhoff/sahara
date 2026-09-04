#!/usr/bin/env python3
"""Turn a user-supplied CSV into a cases file `run_batch.py --cases` can replay.

This is not a second case format. It builds the exact same envelope
`generate_synthetic.py` does — `{"meta": ..., "cases": [{"synthetic_case_ref",
"initial_event", "profile"}, ...]}` — by calling that module's own `make_case()`, so a
row here enters the pipeline through the identical Razorpay-webhook-shaped payload a
generated case does. Nothing downstream (`run_batch.py`, the control room's
`/api/control/batch`) needs to know a case came from a CSV instead of the generator.

Columns, one row per case:

    category            required. One of: card_expired, insufficient_funds,
                         issuer_declined, authentication_failed,
                         invalid_payment_method, unknown.
    category_truth       optional. Defaults to `category`. Set this to grade the
                         diagnoser against a DIFFERENT true cause than the error text
                         implies — the same trick generate_synthetic.py's LLM-fallback
                         rows use.
    amount_rupees        optional. Blank samples from the same weighted distribution
                         the generator uses (docs/05 §3).
    outcome_script        optional, default "roll". One of: roll (use the modelling
                         assumptions in run_batch.py), never, recover_on_attempt_1,
                         recover_on_attempt_2, recover_on_attempt_3, recover_via_link.
    opted_out             optional bool (true/false/1/0/yes/no), default false.
    occurred_hours_ago    optional. Hours before the batch start the failure occurred.
                         Blank samples within the same 7-day window the generator uses.
    error_description     optional. Overrides the category's canned error text with
                         this exact string (source recorded as "customer"). Leave
                         blank to get one of the category's own flavours — or, for
                         `unknown`, a genuinely empty error object, same as the
                         generator's own unknown-with-no-error rows.

Unknown extra columns are ignored, not rejected — a CSV exported from somewhere else
with its own bookkeeping columns should not have to be trimmed by hand first.

    python scripts/csv_to_cases.py --csv my_cases.csv --seed 7 --out my_cases.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from datetime import timedelta
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from scripts.generate_synthetic import (  # noqa: E402
    BATCH_START,
    ERROR_FLAVOURS,
    make_case,
)

OUTCOME_SCRIPTS = {
    "roll", "never", "recover_on_attempt_1", "recover_on_attempt_2",
    "recover_on_attempt_3", "recover_via_link",
}
TRUE_STRINGS = {"1", "true", "yes", "y", "t"}
FALSE_STRINGS = {"0", "false", "no", "n", "f", ""}


class RowError(ValueError):
    """A single row's problem — collected, not raised, so one bad row does not throw
    away every good one in the same file."""


def _parse_bool(raw: str, field: str) -> bool:
    v = raw.strip().lower()
    if v in TRUE_STRINGS:
        return True
    if v in FALSE_STRINGS:
        return False
    raise RowError(f"{field}: {raw!r} is not a recognised true/false value")


def _parse_float(raw: str, field: str) -> Optional[float]:
    v = raw.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        raise RowError(f"{field}: {raw!r} is not a number") from None


def row_to_case(rng: random.Random, index: int, row: dict[str, str]) -> dict[str, Any]:
    """One CSV row -> one case in generate_synthetic.py's own shape.

    Every value the row leaves blank is filled the same way an ungenerated field
    would be inside `make_case()` itself — sampled from the seeded RNG rather than
    defaulted to a fixed constant — so a CSV that only sets `category` on every row
    still produces the same kind of varied batch the generator does.
    """
    category = (row.get("category") or "").strip().lower()
    if not category:
        category = "unknown"
    if category not in config.CATEGORIES:
        raise RowError(
            f"category: {category!r} is not one of {list(config.CATEGORIES)}")

    category_truth = (row.get("category_truth") or "").strip().lower() or category
    if category_truth not in config.CATEGORIES:
        raise RowError(
            f"category_truth: {category_truth!r} is not one of {list(config.CATEGORIES)}")

    outcome_script = (row.get("outcome_script") or "roll").strip().lower() or "roll"
    if outcome_script not in OUTCOME_SCRIPTS:
        raise RowError(
            f"outcome_script: {outcome_script!r} is not one of {sorted(OUTCOME_SCRIPTS)}")

    opted_out = _parse_bool(row.get("opted_out") or "", "opted_out")

    amount_rupees = _parse_float(row.get("amount_rupees") or "", "amount_rupees")
    if amount_rupees is not None and amount_rupees <= 0:
        raise RowError(f"amount_rupees: {amount_rupees} must be positive")
    amount_paise = round(amount_rupees * 100) if amount_rupees is not None else None

    hours_ago = _parse_float(row.get("occurred_hours_ago") or "", "occurred_hours_ago")
    if hours_ago is not None and hours_ago < 0:
        raise RowError(f"occurred_hours_ago: {hours_ago} must not be negative")
    occurred = (BATCH_START - timedelta(hours=hours_ago)) if hours_ago is not None else None

    description = (row.get("error_description") or "").strip()
    error: Optional[tuple[str, str, str, str]] = None
    if description:
        error = ("BAD_REQUEST_ERROR", "payment_failed", description, "customer")
    elif category in ERROR_FLAVOURS:
        error = rng.choice(ERROR_FLAVOURS[category])
    # else: no flavour for this category (only "unknown") and no override -> a
    # genuinely empty error object, same as generate_synthetic.py's own unknown rows.

    case = make_case(
        rng, index, category,
        error=error, category_truth=category_truth, opted_out=opted_out,
        amount_paise=amount_paise, occurred=occurred, outcome_script=outcome_script,
    )
    case["synthetic_case_ref"] = f"SYNTH-CSV-{index:04d}"
    return case


def convert(csv_path: Path, seed: int) -> tuple[dict[str, Any], list[str]]:
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    problems: list[str] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "category" not in {
            (f or "").strip().lower() for f in reader.fieldnames
        }:
            problems.append("no 'category' column found — every row needs one")
            return {"meta": {}, "cases": []}, problems
        # DictReader keys on the header exactly as written; normalise once so a file
        # with "Category" or trailing spaces still works.
        for row_n, raw_row in enumerate(reader, start=2):  # header is line 1
            row = {(k or "").strip().lower(): (v or "") for k, v in raw_row.items()}
            try:
                cases.append(row_to_case(rng, len(cases) + 1, row))
            except RowError as exc:
                problems.append(f"line {row_n}: {exc}")

    meta = {
        "seed": seed,
        "n_sampled": len(cases),
        "n_edge_cases": 0,
        "n_total": len(cases),
        "batch_start": BATCH_START.isoformat().replace("+00:00", "Z"),
        "synthetic": True,
        "source": "user-supplied CSV",
        "source_file": csv_path.name,
        "note": ("All cases are synthetic. Outcome probabilities in run_batch.py are "
                 "modelling assumptions, not measured industry data."),
    }
    return {"meta": meta, "cases": cases}, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert a CSV of failure cases into a cases file")
    ap.add_argument("--csv", required=True, help="path to the input CSV")
    ap.add_argument("--out", default="uploaded_cases.json")
    ap.add_argument("--seed", type=int, default=42,
                    help="seeds every value the CSV leaves blank (amount, timing, error "
                         "flavour), so re-running the same CSV with the same seed "
                         "reproduces the same batch")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        ap.error(f"no such file: {csv_path}")

    data, problems = convert(csv_path, args.seed)

    for p in problems:
        print(f"  ! {p}")

    if not data["cases"]:
        print("wrote nothing: no valid rows")
        return 1

    Path(args.out).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    dist: dict[str, int] = {}
    for c in data["cases"]:
        dist[c["profile"]["category_truth"]] = dist.get(c["profile"]["category_truth"], 0) + 1
    print(f"wrote {args.out}: {len(data['cases'])} cases from {csv_path.name} "
          f"({len(problems)} row(s) skipped), seed {args.seed}")
    for k in sorted(dist):
        print(f"  {k:24s} {dist[k]:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
