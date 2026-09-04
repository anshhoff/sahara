#!/usr/bin/env python3
"""What one escalation rung is worth, with an interval (task 3.10).

Two batches over the identical case file and the identical seed, differing in exactly
one thing: whether attempt 3 places a voice call or hands the case to a person.

    python scripts/run_batch.py --cases C --db on.db  --seed 42 --holdout 0.35
    python scripts/run_batch.py --cases C --db off.db --seed 42 --holdout 0.35 --no-voice
    python scripts/measure_rung.py --with on.db --without off.db

The statistic is a DIFFERENCE IN LIFTS — each batch already has its own control arm,
so this is a difference-in-differences and the organic recovery common to both worlds
cancels out of it twice.

The interval is a bootstrap over cases, resampling all four arms independently and
recomputing the whole quantity each time. It is honest about one thing in particular:
the two runs diverge in the seeded RNG stream the moment their policies differ, so
they are two draws from one world under two policies rather than one world observed
twice. That is exactly what a policy comparison is, and it is why the interval is over
cases rather than a paired test over matched pairs that do not exist.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def arms(path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, status, amount_at_risk_paise, is_holdout FROM recovery_case"
        " ORDER BY created_at, rowid")]
    conn.close()
    return ([r for r in rows if not r["is_holdout"]],
            [r for r in rows if r["is_holdout"]])


def totals(path: str) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {
        "cost_paise": int(conn.execute(
            "SELECT COALESCE(SUM(cost_paise), 0) FROM execution_record").fetchone()[0]),
        "by_action": {r["action"]: int(r["n"]) for r in conn.execute(
            "SELECT action, COUNT(*) n FROM execution_record GROUP BY action")},
        "n_handoff": int(conn.execute(
            "SELECT COUNT(*) FROM recovery_case WHERE status IN"
            " ('stopped_handoff','stopped_max_attempts','stopped_cooldown_expired',"
            "  'stopped_unknown')").fetchone()[0]),
        "promises": {r["status"]: int(r["n"]) for r in conn.execute(
            "SELECT status, COUNT(*) n FROM promise GROUP BY status")},
    }
    conn.close()
    return out


def _rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for r in rows if r["status"] == "recovered") / len(rows) if rows else 0.0


def _money_per_case(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(int(r["amount_at_risk_paise"]) for r in rows
               if r["status"] == "recovered") / len(rows)


def compare(with_db: str, without_db: str, bootstrap: int = 10000, seed: int = 42) -> dict[str, Any]:
    on_t, on_c = arms(with_db)
    off_t, off_c = arms(without_db)
    if not (on_c and off_c):
        raise SystemExit("both batches need a control arm — rerun with --holdout")

    lift_on = _rate(on_t) - _rate(on_c)
    lift_off = _rate(off_t) - _rate(off_c)
    money_on = (_money_per_case(on_t) - _money_per_case(on_c)) * len(on_t)
    money_off = (_money_per_case(off_t) - _money_per_case(off_c)) * len(off_t)

    on_cost, off_cost = totals(with_db)["cost_paise"], totals(without_db)["cost_paise"]

    rng = random.Random(seed)
    lift_deltas: list[float] = []
    money_deltas: list[float] = []
    for _ in range(bootstrap):
        def draw(rows):
            return [rows[rng.randrange(len(rows))] for _ in range(len(rows))]

        bt, bc = draw(on_t), draw(on_c)
        ct, cc = draw(off_t), draw(off_c)
        lift_deltas.append((_rate(bt) - _rate(bc)) - (_rate(ct) - _rate(cc)))
        money_deltas.append(
            (_money_per_case(bt) - _money_per_case(bc)) * len(bt)
            - (_money_per_case(ct) - _money_per_case(cc)) * len(ct))

    def ci(xs: list[float]) -> tuple[float, float]:
        xs = sorted(xs)
        return xs[int(0.025 * len(xs))], xs[min(int(0.975 * len(xs)), len(xs) - 1)]

    lift_lo, lift_hi = ci(lift_deltas)
    money_lo, money_hi = ci(money_deltas)
    # The cost delta is a known quantity, not a sampled one — the batches spent what they
    # spent — so it shifts the money interval rather than widening it.
    cost_delta = on_cost - off_cost

    return {
        "with_voice": {
            "db": with_db, "lift": round(lift_on, 4),
            "treated": f"{sum(1 for r in on_t if r['status'] == 'recovered')}/{len(on_t)}",
            "control": f"{sum(1 for r in on_c if r['status'] == 'recovered')}/{len(on_c)}",
            "incremental_paise": int(round(money_on)),
            "cost_paise": on_cost, **totals(with_db),
        },
        "without_voice": {
            "db": without_db, "lift": round(lift_off, 4),
            "treated": f"{sum(1 for r in off_t if r['status'] == 'recovered')}/{len(off_t)}",
            "control": f"{sum(1 for r in off_c if r['status'] == 'recovered')}/{len(off_c)}",
            "incremental_paise": int(round(money_off)),
            "cost_paise": off_cost, **totals(without_db),
        },
        "delta": {
            "lift_pp": round((lift_on - lift_off) * 100, 2),
            "lift_pp_ci95": [round(lift_lo * 100, 2), round(lift_hi * 100, 2)],
            "lift_significant": lift_lo > 0 or lift_hi < 0,
            "incremental_paise": int(round(money_on - money_off)),
            "incremental_paise_ci95": [int(round(money_lo)), int(round(money_hi))],
            "cost_paise": cost_delta,
            "net_incremental_paise": int(round(money_on - money_off)) - cost_delta,
            "net_incremental_paise_ci95": [int(round(money_lo)) - cost_delta,
                                           int(round(money_hi)) - cost_delta],
            "net_significant": (money_lo - cost_delta) > 0 or (money_hi - cost_delta) < 0,
        },
        "method": {
            "statistic": ("difference in lifts — each batch carries its own control arm, so "
                          "this is a difference-in-differences and the organic recovery "
                          "common to both worlds cancels out twice"),
            "interval": f"percentile bootstrap over cases, {bootstrap} resamples, seed {seed}",
            "caveat": ("the two runs diverge in the seeded RNG stream the moment their "
                       "policies differ, so they are two draws from one world under two "
                       "policies rather than one world observed twice. That is what a policy "
                       "comparison is; it is also why there is no paired test here, because "
                       "there are no matched pairs to pair."),
        },
    }


def print_report(r: dict[str, Any]) -> None:
    on, off, d = r["with_voice"], r["without_voice"], r["delta"]
    print("\n" + "=" * 78)
    print("WHAT THE VOICE RUNG IS WORTH")
    print("=" * 78)
    print(f"  {'':22s} {'with voice':>22s} {'without':>22s}")
    print("  " + "-" * 68)
    print(f"  {'treated recovered':22s} {on['treated']:>22s} {off['treated']:>22s}")
    print(f"  {'control recovered':22s} {on['control']:>22s} {off['control']:>22s}")
    print(f"  {'lift':22s} {on['lift'] * 100:>21.1f}pp {off['lift'] * 100:>21.1f}pp")
    print(f"  {'incremental':22s} {'Rs ' + format(on['incremental_paise'] / 100, ',.0f'):>22s}"
          f" {'Rs ' + format(off['incremental_paise'] / 100, ',.0f'):>22s}")
    print(f"  {'outreach cost':22s} {'Rs ' + format(on['cost_paise'] / 100, ',.0f'):>22s}"
          f" {'Rs ' + format(off['cost_paise'] / 100, ',.0f'):>22s}")
    print(f"  {'human queue cases':22s} {on['n_handoff']:>22d} {off['n_handoff']:>22d}")
    print(f"  {'voice calls':22s} {on['by_action'].get('VOICE_CALL', 0):>22d}"
          f" {off['by_action'].get('VOICE_CALL', 0):>22d}")
    kept, broken = on["promises"].get("kept", 0), on["promises"].get("broken", 0)
    print(f"  {'dated promises':22s} {f'{kept} kept / {broken} broken':>22s} {'-':>22s}")
    print("\n  THE DELTA")
    lo, hi = d["lift_pp_ci95"]
    print(f"    lift              {d['lift_pp']:+.1f} pp   95% CI [{lo:+.1f}, {hi:+.1f}] pp"
          f"   ({'excludes' if d['lift_significant'] else 'includes'} zero)")
    mlo, mhi = d["net_incremental_paise_ci95"]
    print(f"    net incremental   Rs {d['net_incremental_paise'] / 100:+,.0f}"
          f"   95% CI [Rs {mlo / 100:+,.0f}, Rs {mhi / 100:+,.0f}]"
          f"   ({'excludes' if d['net_significant'] else 'includes'} zero)")
    print(f"    extra spend       Rs {d['cost_paise'] / 100:+,.0f}"
          f"   ({on['by_action'].get('VOICE_CALL', 0)} calls at Rs "
          f"{25}, {off['n_handoff'] - on['n_handoff']:+d} fewer cases in the human queue)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--with", dest="with_db", required=True)
    ap.add_argument("--without", dest="without_db", required=True)
    ap.add_argument("--out", default="docs/voice-rung.json")
    ap.add_argument("--bootstrap", type=int, default=10000)
    args = ap.parse_args()

    result = compare(args.with_db, args.without_db, args.bootstrap)
    print_report(result)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
