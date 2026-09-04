#!/usr/bin/env python3
"""The drift guard (task 6.3).

Re-runs the batch from the seed in a throwaway database, extracts every number this
project publishes, and byte-compares it against the committed JSON. Editing a digit in
the README's results table — or changing a constant that moves one — makes this exit
non-zero.

    python scripts/verify_numbers.py            # re-derive and print
    python scripts/verify_numbers.py --check    # re-derive, compare, exit non-zero on drift
    python scripts/verify_numbers.py --update   # accept the new numbers, deliberately

The point is not that numbers must never change. Numbers change whenever a
measurement is fixed, and this project has already moved every one of its headlines
twice for exactly that reason. The point is that they cannot change **quietly**:
`--update` is a commit somebody has to make and explain, and CI is red until they do.

WHY IT RE-RUNS RATHER THAN READING recovery.db
    A checked-in database can be edited. Re-deriving from the seed means the committed
    numbers are checked against the code that is in the tree right now, which is the
    only version of this check that catches a constant somebody changed without
    noticing what it moved.

WHAT IT DOES NOT CHECK
    Case ids. They carry a random ULID tail, so two clean-room runs of the same seed
    produce identical numbers and different ids. The seed is what reproduces; the audit
    chain's head is what detects tampering, and it is deliberately absent from this
    file for the same reason.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COMMITTED = ROOT / "docs" / "verified-numbers.json"

# The batch every published figure comes from. Changing any of these is changing what
# the README is about, which is why they live here rather than in a shell script.
N_CASES = 80
SEED = 42
HOLDOUT = 0.35


def _round(x: Any, places: int = 4) -> Any:
    return round(x, places) if isinstance(x, float) else x


def headline_numbers(summary: dict[str, Any], accuracy: dict[str, Any]) -> dict[str, Any]:
    """Every number this project stands behind, and nothing that is merely incidental.

    Deliberately excluded: timestamps, ids, hashes, latency percentiles, and anything
    else that legitimately differs between two identical runs. A drift guard that goes
    red on the wall clock is a drift guard people learn to ignore.
    """
    inc = summary["incremental"]
    net = summary["net"]
    cal = summary["calibration"]
    out: dict[str, Any] = {
        "n_cases": summary["n_cases"],
        "seed": summary["seed"],
        "total_at_risk_paise": summary["total_at_risk_paise"],
        "total_recovered_paise": summary["total_recovered_paise"],
        "recovery_rate": _round(summary["recovery_rate"]["rate"]),
        "n_recovered": summary["n_recovered"],
        "n_open": summary["n_open"],
        "stopped_by_status": summary["stopped"]["by_status"],
        "costs": {
            "outreach_paise": summary["costs"]["outreach_paise"],
            "handoff_paise": summary["costs"]["handoff_paise"],
            "total_paise": summary["costs"]["total_paise"],
            "n_handoff_cases": summary["costs"]["n_handoff_cases"],
        },
        "net": {
            "gross_recovered_paise": net["gross_recovered_paise"],
            "net_recovered_paise": net["net_recovered_paise"],
            "cost_per_100_recovered": _round(net["cost_per_100_recovered"], 2),
        },
        "executions_by_action": summary["executions_by_action"],
        "declined_to_contact": summary["declined_to_contact"]["n_declined"],
        "fencing": {
            "outreach_to_settled": summary["fencing"]["outreach_to_settled"],
            "n_dispatches_fenced": summary["fencing"]["n_dispatches_fenced"],
            "n_compensations": summary["fencing"]["n_compensations"],
        },
        "promises": {
            "n_promises": summary["promises"]["n_promises"],
            "promises_kept": summary["promises"]["promises_kept"],
            "promises_broken": summary["promises"]["promises_broken"],
        },
        "audit_chain": {
            "status": summary["audit_chain"]["status"],
            "n_entries": summary["audit_chain"]["n_entries"],
            "n_unchained": summary["audit_chain"]["n_unchained"],
        },
        "classification": {
            method: {"n": b["n"], "correct": b["correct"], "accuracy": _round(b["accuracy"])}
            for method, b in accuracy["by_method"].items()
        },
    }
    if inc.get("available"):
        out["incremental"] = {
            "treated": {k: _round(v) for k, v in inc["treated"].items()},
            "control": {k: _round(v) for k, v in inc["control"].items()},
            "lift": _round(inc["lift"]),
            "lift_ci95": [_round(x) for x in inc["lift_ci95"]],
            "significant": inc["significant"],
            "incremental_paise_total": inc["incremental_paise_total"],
            "incremental_paise_ci95": inc["incremental_paise_ci95"],
        }
        out["net"]["net_incremental_paise"] = net["net_incremental_paise"]
        out["net"]["net_incremental_paise_ci95"] = net["net_incremental_paise_ci95"]
    if cal.get("available"):
        out["calibration"] = {
            "brier_score": _round(cal["brier_score"]),
            "ece": _round(cal["ece"]),
            "n_scored": cal["n_scored"],
        }
    out["lift_by_category"] = [
        {
            "category": r["category"],
            "treated_n": r["treated"]["n"],
            "treated_recovered": r["treated"]["recovered"],
            "control_n": r["control"]["n"],
            "control_recovered": r["control"]["recovered"],
            "lift": _round(r["lift"]),
            "lift_ci95": None if r["lift_ci95"] is None else [_round(x) for x in r["lift_ci95"]],
        }
        for r in summary.get("lift_by_category", [])
    ]
    return out


def rerun() -> dict[str, Any]:
    """Replay the batch from the seed into a throwaway database and pull the numbers."""
    with tempfile.TemporaryDirectory() as tmp:
        cases = Path(tmp) / "cases.json"
        db_path = Path(tmp) / "verify.db"
        env = dict(os.environ)
        env["LLM_PROVIDER"] = "none"        # a model would make this non-deterministic
        env["DB_PATH"] = str(db_path)

        for argv in (
            [sys.executable, "scripts/generate_synthetic.py", "--n", str(N_CASES),
             "--seed", str(SEED), "--out", str(cases)],
            [sys.executable, "scripts/run_batch.py", "--cases", str(cases),
             "--db", str(db_path), "--seed", str(SEED), "--holdout", str(HOLDOUT)],
        ):
            result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                sys.stderr.write(result.stdout + result.stderr)
                raise SystemExit(f"{argv[1]} exited {result.returncode}")

        # Read through the same metrics module the API serves, not by querying SQL here.
        # A second implementation of the numbers would be a second thing that can be
        # wrong, and the one being verified is the one the dashboard shows.
        from app import db, metrics

        db.init(str(db_path))
        summary = metrics.summary()
        run = db.query_one("SELECT summary FROM batch_run ORDER BY id DESC LIMIT 1")
        accuracy = json.loads(run["summary"])["accuracy"] if run and run["summary"] else {
            "by_method": {}}
        db.close()
        return headline_numbers(summary, accuracy)


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Leaf paths, so a diff names the field rather than dumping two documents."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff(committed: dict[str, Any], derived: dict[str, Any]) -> list[str]:
    a, b = _flatten(committed), _flatten(derived)
    lines = []
    for key in sorted(set(a) | set(b)):
        if key not in a:
            lines.append(f"  + {key} = {b[key]!r}  (new)")
        elif key not in b:
            lines.append(f"  - {key} = {a[key]!r}  (gone)")
        elif a[key] != b[key]:
            lines.append(f"  ~ {key}: committed {a[key]!r} -> now {b[key]!r}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed file and exit non-zero on drift")
    ap.add_argument("--update", action="store_true",
                    help="accept the re-derived numbers as the new committed set")
    args = ap.parse_args()

    print(f"re-deriving from seed {SEED} (n={N_CASES}, holdout={HOLDOUT}, LLM_PROVIDER=none) ...")
    derived = rerun()

    if args.update or not COMMITTED.exists():
        COMMITTED.parent.mkdir(parents=True, exist_ok=True)
        COMMITTED.write_text(json.dumps(derived, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {COMMITTED.relative_to(ROOT)}")
        if not args.update:
            print("  (the file did not exist; nothing was compared)")
        return 0

    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    lines = diff(committed, derived)

    if not lines:
        print(f"\n  {len(_flatten(derived))} published values match "
              f"{COMMITTED.relative_to(ROOT)} exactly.")
        return 0

    print(f"\n  DRIFT — {len(lines)} value(s) no longer match "
          f"{COMMITTED.relative_to(ROOT)}:\n")
    for line in lines:
        print(line)
    print("\n  If this is a measurement fix, the numbers SHOULD move. Run")
    print("      python scripts/verify_numbers.py --update")
    print("  and commit the change with the reason — keeping the superseded output in")
    print("  the repository, as docs/19-decision-log.md requires. If it is not a fix,")
    print("  something moved a number nobody meant to move, which is what this exists for.")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
