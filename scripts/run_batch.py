#!/usr/bin/env python3
"""Batch runner (docs/05 §6–§7).

The runner talks to the pipeline through exactly two functions — `intake()` and
`tick()`. It never writes an application table directly. That is the point: the batch
numbers are produced by the same code the live webhook path runs, so they are a claim
about the pipeline rather than about a parallel test harness.

The outcome model below (whether a simulated retry or link "worked") is a set of
MODELLING ASSUMPTIONS, seeded for reproducibility. They are not measured industry
data and are labelled as such everywhere they surface.

    python scripts/run_batch.py --cases synthetic_cases.json --db recovery.db [--live-links 5]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cases as case_store  # noqa: E402
from app import clock, config, db, executor, llm, metrics, webhooks  # noqa: E402

# --------------------------------------------------------------- outcome model
# (category, action) -> probability of the money actually arriving, by attempt.
# docs/05 §6. Assumptions, not measurements.
SUCCESS_PROBABILITY: dict[tuple[str, str], list[float]] = {
    ("insufficient_funds", "RETRY_LATER"): [0.45, 0.35, 0.35],
    ("insufficient_funds", "PROMISE_TO_PAY"): [0.50, 0.50, 0.50],
    ("insufficient_funds", "SEND_UPDATE_LINK"): [0.35, 0.20, 0.20],
    ("card_expired", "SEND_UPDATE_LINK"): [0.40, 0.20, 0.20],
    ("issuer_declined", "RETRY_LATER"): [0.30, 0.30, 0.30],
    ("issuer_declined", "SEND_UPDATE_LINK"): [0.35, 0.25, 0.25],
    ("authentication_failed", "SEND_UPDATE_LINK"): [0.45, 0.25, 0.25],
    ("invalid_payment_method", "SEND_UPDATE_LINK"): [0.30, 0.20, 0.20],
}
DEFAULT_FOLLOWUP_HOURS = 24  # next dunning cycle after an unanswered contact
RETRY_RESULT_HOURS = 1       # a silent re-charge resolves quickly

# Baseline: the chance a failed charge comes back WITHOUT any intervention, inside the
# episode window — the customer tops up, or the issuer stops declining, and Razorpay's
# own retry then succeeds. This is the counterfactual the control arm exists to
# measure, and it is the single most consequential assumption in the whole batch:
# set it to zero and the agent appears to earn every rupee it touches.
#
# The ordering is the defensible part, not the exact values. An expired card does not
# un-expire, so card_expired is near zero; an empty account often refills by payday,
# so insufficient_funds is the highest. Assumptions, not measurements (docs/05 §6).
BASELINE_RECOVERY_PROBABILITY: dict[str, float] = {
    "insufficient_funds": 0.22,
    "issuer_declined": 0.14,
    "authentication_failed": 0.09,
    "unknown": 0.06,
    "invalid_payment_method": 0.03,
    "card_expired": 0.02,
}


class Runner:
    def __init__(self, data: dict[str, Any], rng: random.Random, live_links: int,
                 holdout_fraction: float = 0.0):
        self.meta = data.get("meta", {})
        self.cases = data["cases"]
        self.rng = rng
        self.profiles: dict[str, dict[str, Any]] = {}
        self.seen_executions: set[str] = set()
        self.pending: list[tuple[str, dict[str, Any]]] = []  # (due_iso, payload)
        self.event_counter: dict[str, int] = {}
        self.opted_out_done: set[str] = set()
        self.holdout_fraction = holdout_fraction
        # A LIST, not a set: this is iterated while consuming the seeded RNG, and set
        # iteration order over strings varies per process (hash randomisation), which
        # would make an identical seed produce different numbers on every run.
        self.control_subs: list[str] = []
        self.baseline_rolled: set[str] = set()
        executor.set_live_link_budget(live_links)

    # ------------------------------------------------------------- utilities
    def _sub_id(self, case: dict[str, Any]) -> str:
        return case["initial_event"]["payload"]["subscription"]["entity"]["id"]

    def _next_event_id(self, sub_id: str) -> str:
        n = self.event_counter.get(sub_id, 1) + 1
        self.event_counter[sub_id] = n
        return f"SYNTH-evt-{sub_id.split('-')[-1]}-{n}"

    def _profile_for_case_id(self, case_id: str) -> dict[str, Any]:
        row = db.query_one("SELECT subscription_id FROM recovery_case WHERE id = ?", (case_id,))
        return self.profiles.get(row["subscription_id"], {}) if row else {}

    def _enqueue(self, due, payload: dict[str, Any]) -> None:
        self.pending.append((clock.to_iso(due), payload))

    # --------------------------------------------------------------- payloads
    def _followup_failure(self, sub_id: str, when) -> dict[str, Any]:
        """The next dunning cycle failing again, with the same underlying cause."""
        profile = self.profiles[sub_id]
        template = json.loads(json.dumps(profile["initial_event"]))
        entity = template["payload"]["payment"]["entity"]
        seq = self.event_counter.get(sub_id, 1) + 1
        template["id"] = self._next_event_id(sub_id)
        template["event"] = "subscription.pending"
        template["created_at"] = int(when.timestamp())
        entity["id"] = f"{entity['id']}-r{seq}"
        entity["created_at"] = int(when.timestamp())
        template["payload"]["subscription"]["entity"]["status"] = "pending"
        return template

    def _recovery_event(self, sub_id: str, case_id: str, event_type: str, when) -> dict[str, Any]:
        profile = self.profiles[sub_id]
        base = profile["initial_event"]
        cust_id = base["payload"]["payment"]["entity"]["customer_id"]
        amount = profile["profile"]["amount_paise"]
        pay_id = f"SYNTH-pay-recovered-{sub_id.split('-')[-1]}"
        payment_entity = {
            "id": pay_id,
            "entity": "payment",
            "amount": amount,
            "currency": "INR",
            "status": "captured",
            "method": "card",
            "customer_id": cust_id,
            "captured": True,
            "created_at": int(when.timestamp()),
            "notes": {"subscription_id": sub_id, "customer_id": cust_id, "case_id": case_id},
        }
        payload = {
            "entity": "event",
            "account_id": "acc_SYNTHETIC",
            "event": event_type,
            "contains": ["payment", "subscription"],
            "payload": {
                "payment": {"entity": payment_entity},
                "subscription": {"entity": {"id": sub_id, "entity": "subscription",
                                            "customer_id": cust_id, "status": "active"}},
            },
            "created_at": int(when.timestamp()),
            "id": self._next_event_id(sub_id),
            "synthetic": True,
        }
        if event_type == "payment_link.paid":
            payload["payload"]["payment_link"] = {
                "entity": {
                    "id": f"SYNTH-plink-{sub_id.split('-')[-1]}",
                    "entity": "payment_link",
                    "amount": amount,
                    "currency": "INR",
                    "status": "paid",
                    "reference_id": sub_id,
                    "notes": {"case_id": case_id, "subscription_id": sub_id},
                }
            }
            payload["contains"].append("payment_link")
        return payload

    # --------------------------------------------------------- outcome model
    def _succeeds(self, profile: dict[str, Any], category: str, action: str, attempt: int) -> bool:
        script = profile.get("outcome_script", "roll")
        if script == "never":
            self.rng.random()  # keep the RNG stream aligned regardless of script
            return False
        if script.startswith("recover_on_attempt_"):
            self.rng.random()
            return attempt == int(script.rsplit("_", 1)[1])
        if script == "recover_via_link":
            self.rng.random()
            return action in config.CONTACT_ACTIONS
        probs = SUCCESS_PROBABILITY.get((category, action), [0.25, 0.15, 0.15])
        p = probs[min(attempt, len(probs)) - 1]
        return self.rng.random() < p

    def roll_baseline_for_control(self) -> None:
        """Decide, once per control case, whether it recovers on its own.

        A control case is never executed against, so process_new_executions() never
        sees it. Its outcome is drawn here instead, from BASELINE_RECOVERY_PROBABILITY,
        and lands as an ordinary recovery webhook at a random point in the window —
        the same event type, through the same intake(), as any treated recovery.
        """
        for sub_id in self.control_subs:
            if sub_id in self.baseline_rolled:
                continue
            case = case_store.find_latest_by_subscription(sub_id)
            if case is None or not case["current_category"]:
                continue          # not diagnosed yet; roll on a later pass
            self.baseline_rolled.add(sub_id)
            p = BASELINE_RECOVERY_PROBABILITY.get(case["current_category"], 0.05)
            if self.rng.random() >= p:
                continue
            # Self-recovery is slow: it waits on a payday or an issuer, not on us.
            when = clock.parse_iso(case["created_at"]) + timedelta(
                hours=self.rng.randint(24, config.EPISODE_WINDOW_DAYS * 24 - 1))
            self._enqueue(when, self._recovery_event(
                sub_id, case["id"], "subscription.charged", when))

    def process_new_executions(self) -> None:
        rows = db.query("SELECT * FROM execution_record ORDER BY rowid")
        for row in rows:
            if row["id"] in self.seen_executions:
                continue
            self.seen_executions.add(row["id"])
            record = db.row_to_dict(row)
            case = case_store.get(record["case_id"])
            profile_entry = self._profile_for_case_id(record["case_id"])
            if not profile_entry:
                continue
            profile = profile_entry["profile"]
            sub_id = case["subscription_id"]
            decision = db.row_to_dict(db.query_one(
                "SELECT * FROM intervention_decision WHERE id = ?", (record["decision_id"],)))
            attempt = int(decision["attempt_number"])
            category = case["current_category"] or "unknown"
            executed_at = clock.parse_iso(record["executed_at"])

            if self._succeeds(profile, category, record["action"], attempt):
                if record["action"] == "RETRY_LATER":
                    when = executed_at + timedelta(hours=RETRY_RESULT_HOURS)
                    event_type = "subscription.charged"
                elif record["action"] == "PROMISE_TO_PAY":
                    when = executed_at + timedelta(
                        hours=self.rng.randint(12, config.PROMISE_WINDOW_HOURS - 1))
                    event_type = "payment_link.paid"
                else:
                    when = executed_at + timedelta(hours=self.rng.randint(2, 48))
                    event_type = "payment_link.paid"
                self._enqueue(when, self._recovery_event(sub_id, case["id"], event_type, when))
                continue

            # Not recovered. A silent retry fails fast and produces the next failure
            # event; an unanswered contact produces the next dunning-cycle failure.
            # A lapsed promise produces nothing: tick() hands it off at the deadline.
            if record["action"] == "RETRY_LATER":
                when = executed_at + timedelta(hours=RETRY_RESULT_HOURS)
                self._enqueue(when, self._followup_failure(sub_id, when))
            elif record["action"] == "SEND_UPDATE_LINK":
                hours = int(profile.get("followup_delay_hours") or DEFAULT_FOLLOWUP_HOURS)
                when = executed_at + timedelta(hours=hours)
                self._enqueue(when, self._followup_failure(sub_id, when))

    def deliver_due_events(self) -> int:
        now = clock.now_iso()
        due = [(t, p) for t, p in self.pending if t <= now]
        self.pending = [(t, p) for t, p in self.pending if t > now]
        for _, payload in sorted(due, key=lambda x: x[0]):
            webhooks.intake(payload, source="synthetic")
        return len(due)

    def apply_mid_flight_opt_outs(self) -> None:
        """SYNTH-E-02: flip the flag after attempt 1 has executed but before the next
        one runs, so the pre-execution re-check has something real to catch."""
        for sub_id, entry in self.profiles.items():
            after = entry["profile"].get("opt_out_after_attempt")
            if not after or sub_id in self.opted_out_done:
                continue
            case = case_store.find_latest_by_subscription(sub_id)
            if not case or case["status"] != "open" or int(case["attempt_count"]) < int(after):
                continue
            # Wait until the NEXT attempt is already decided and waiting to run, so the
            # opt-out lands between the decision and its execution. That is the only
            # way to prove the pre-execution re-check is not theatre.
            pending = db.query_one(
                "SELECT 1 FROM intervention_decision WHERE case_id = ? AND status = 'scheduled' LIMIT 1",
                (case["id"],))
            if pending is None:
                continue
            case_store.set_opted_out(case["id"], True)
            self.opted_out_done.add(sub_id)

    # ------------------------------------------------------------------- run
    def run(self) -> None:
        for case in self.cases:
            sub_id = self._sub_id(case)
            self.profiles[sub_id] = case
            self.event_counter[sub_id] = 1
            # Randomised assignment, drawn from the same seeded stream as every other
            # decision in the batch, so an arm split is reproducible like anything else.
            if self.holdout_fraction > 0 and self.rng.random() < self.holdout_fraction:
                self.control_subs.append(sub_id)
                case["initial_event"] = json.loads(json.dumps(case["initial_event"]))
                case["initial_event"]["holdout"] = True
            webhooks.intake(case["initial_event"], source="synthetic")
            if case["profile"].get("duplicate_delivery"):
                # Exactly the same event id, delivered twice (SYNTH-E-06).
                webhooks.intake(case["initial_event"], source="synthetic")
        self.process_new_executions()
        self.roll_baseline_for_control()

        start = clock.now()
        sim = clock.get_clock()
        deadline = start + timedelta(days=config.EPISODE_WINDOW_DAYS)
        while clock.now() <= deadline:
            if not db.query_one("SELECT 1 FROM recovery_case WHERE status = 'open' LIMIT 1"):
                break
            self.deliver_due_events()
            executor.tick()
            self.process_new_executions()
            self.roll_baseline_for_control()
            self.apply_mid_flight_opt_outs()
            sim.advance(hours=1)
        executor.tick()  # final sweep: close anything the last hour made due


# ------------------------------------------------------------ acceptance checks
def acceptance_checks() -> list[tuple[str, bool, str]]:
    """docs/05 §7. A batch run that violates any of these exits non-zero."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    bad = db.query(f"SELECT id, attempt_count FROM recovery_case WHERE attempt_count > {config.MAX_ATTEMPTS}")
    check(f"I1: no case exceeds {config.MAX_ATTEMPTS} attempts", not bad,
          ", ".join(r["id"] for r in bad))

    bad = db.query(
        "SELECT e.id AS id FROM execution_record e JOIN recovery_case c ON c.id = e.case_id"
        " WHERE c.status = 'stopped_opt_out' AND e.executed_at > c.closed_at"
    )
    check("I3: no execution after an opt-out stop", not bad, ", ".join(r["id"] for r in bad))

    bad = db.query(
        "SELECT e.id AS id FROM execution_record e JOIN recovery_case c ON c.id = e.case_id"
        " WHERE c.status = 'stopped_unknown' AND e.action IN ('SEND_UPDATE_LINK','PROMISE_TO_PAY')"
    )
    check("I4: unknown cases have zero contact executions", not bad, ", ".join(r["id"] for r in bad))

    violations = []
    rows = db.query(
        "SELECT case_id, executed_at FROM execution_record"
        " WHERE action IN ('SEND_UPDATE_LINK','PROMISE_TO_PAY') ORDER BY case_id, executed_at"
    )
    last: dict[str, Any] = {}
    for r in rows:
        prev = last.get(r["case_id"])
        now = clock.parse_iso(r["executed_at"])
        if prev is not None and (now - prev).total_seconds() < config.COOLDOWN_HOURS * 3600 - 1:
            violations.append(f"{r['case_id']} @ {r['executed_at']}")
        last[r["case_id"]] = now
    check(f"I2: contacts on a case are >= {config.COOLDOWN_HOURS}h apart", not violations,
          ", ".join(violations))

    gaps = []
    for row in db.query("SELECT id FROM recovery_case"):
        seqs = [r["seq"] for r in db.query(
            "SELECT seq FROM audit_log WHERE case_id = ? ORDER BY seq", (row["id"],))]
        if seqs != list(range(1, len(seqs) + 1)):
            gaps.append(row["id"])
    check("audit seq is gapless from 1 on every case", not gaps, ", ".join(gaps))

    missing = []
    for row in db.query("SELECT id, status FROM recovery_case WHERE status != 'open'"):
        last_stage = db.scalar(
            "SELECT stage FROM audit_log WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (row["id"],))
        if last_stage not in ("stop", "outcome"):
            missing.append(f"{row['id']}({last_stage})")
    check("every closed case ends on a stop/outcome entry", not missing, ", ".join(missing))

    non_synth = int(db.scalar(
        "SELECT COUNT(*) FROM recovery_case WHERE synthetic = 0", (), 0)) + int(db.scalar(
        "SELECT COUNT(*) FROM failure_event WHERE synthetic = 0", (), 0)) + int(db.scalar(
        "SELECT COUNT(*) FROM execution_record WHERE synthetic = 0", (), 0)) + int(db.scalar(
        "SELECT COUNT(*) FROM audit_log WHERE synthetic = 0", (), 0))
    check("synthetic = 1 on 100% of batch-derived rows", non_synth == 0, f"{non_synth} rows not flagged")

    bad = db.query(
        "SELECT e.id AS id FROM execution_record e JOIN recovery_case c ON c.id = e.case_id"
        " WHERE c.is_holdout = 1")
    check("control arm received zero interventions", not bad, ", ".join(r["id"] for r in bad))

    bad = db.query(
        "SELECT id FROM recovery_case WHERE is_holdout = 1 AND attempt_count > 0")
    check("control arm consumed zero attempts", not bad, ", ".join(r["id"] for r in bad))

    rec = metrics.reconciliation()
    check("reconciliation: recovered + stopped + open == cases", rec["counts_balance"], json.dumps(rec))
    check("reconciliation: recovered <= at risk", rec["amounts_balance"], "")

    return results


def classification_accuracy(runner: Runner) -> dict[str, Any]:
    """Diagnosed category vs the generator's ground truth, reported separately for
    the rule path and the model path (docs/05 §7)."""
    buckets = {"rule": {"n": 0, "correct": 0}, "llm": {"n": 0, "correct": 0}}
    misses: list[str] = []
    for sub_id, entry in runner.profiles.items():
        case = case_store.find_latest_by_subscription(sub_id)
        if case is None:
            continue
        row = db.query_one(
            "SELECT category, method, matched_rule FROM diagnosis_result WHERE case_id = ?"
            " ORDER BY rowid LIMIT 1",
            (case["id"],))
        if row is None:
            continue
        truth = entry["profile"]["category_truth"]
        # A case the rules could not reach belongs to the model path even when there
        # is no model configured — otherwise LLM_PROVIDER=none would flatter the rules.
        path = "llm" if (row["method"] == "llm" or row["matched_rule"] == "R7-no-llm") else "rule"
        bucket = buckets[path]
        bucket["n"] += 1
        if row["category"] == truth:
            bucket["correct"] += 1
        else:
            misses.append(f"{entry['synthetic_case_ref']}: truth={truth} got={row['category']} ({path})")
    for b in buckets.values():
        b["accuracy"] = round(b["correct"] / b["n"], 4) if b["n"] else None
    return {"by_method": buckets, "misses": misses}


def print_summary(runner: Runner, accuracy: dict[str, Any]) -> None:
    s = metrics.summary()
    rate = s["recovery_rate"]
    print("\n" + "=" * 72)
    print("BATCH SUMMARY — all cases synthetic; outcome probabilities are assumptions")
    print("=" * 72)
    print(f"  cases                  {s['n_cases']}  (synthetic {s['n_synthetic']}, live {s['n_live']}, "
          f"seed {s['seed']})")
    print(f"  Rs at risk             {s['total_at_risk_rupees']:,.2f}")
    print(f"  Rs recovered           {s['total_recovered_rupees']:,.2f}")
    print(f"  recovery rate          {rate['rate'] * 100:.1f}%  ({rate['numerator']}/{rate['denominator']} closed)"
          f"   strict {rate['strict_rate'] * 100:.1f}% ({rate['numerator']}/{rate['strict_denominator']})")
    ttr = s["avg_time_to_recovery_hours"]
    print(f"  avg time to recovery   {ttr if ttr is not None else '-'} h ({s['time_basis']})")
    print(f"  still open             {s['n_open']}")
    print("  terminal states:")
    print(f"    {'recovered':28s} {s['n_recovered']}")
    for status, n in sorted(s["stopped"]["by_status"].items()):
        print(f"    {status:28s} {n}")
    llm = s["llm"]
    print(f"  diagnosis: {llm['rule_classified']} by rule, {llm['classified']} by model "
          f"({llm['classified_to_unknown']} of those collapsed to unknown)")
    print(f"  copy: {llm['drafted']} model drafts accepted, {llm['fallback_to_template']} static templates")
    print(f"  executions: {s['execution_modes']}")
    acc = accuracy["by_method"]
    for method in ("rule", "llm"):
        b = acc[method]
        pct = "-" if b["accuracy"] is None else f"{b['accuracy'] * 100:.1f}%"
        print(f"  classification accuracy ({method}): {pct}  ({b['correct']}/{b['n']})")
    inc = s.get("incremental") or {}
    if inc.get("available"):
        t, c = inc["treated"], inc["control"]
        lo, hi = inc["lift_ci95"]
        print("\n  INCREMENTAL RECOVERY (randomised control arm, intention-to-treat)")
        print(f"    treated       {t['recovered']}/{t['n']}  = {t['rate'] * 100:.1f}%")
        print(f"    control       {c['recovered']}/{c['n']}  = {c['rate'] * 100:.1f}%")
        print(f"    lift          {inc['lift'] * 100:+.1f} pp   95% CI [{lo * 100:+.1f}, {hi * 100:+.1f}] pp"
              f"   ({'excludes' if inc['significant'] else 'includes'} zero)")
        mlo, mhi = inc["incremental_paise_ci95"]
        print(f"    incremental   Rs {inc['incremental_paise_total'] / 100:,.0f}"
              f"   95% CI [Rs {mlo / 100:,.0f}, Rs {mhi / 100:,.0f}]"
              f"   of gross Rs {inc['gross_recovered_paise'] / 100:,.0f}")
        print("    NOTE: outcome probabilities are assumptions; this measures the mechanism,")
        print("          not the market. The interval is sampling uncertainty only.")
    if accuracy["misses"]:
        print("  misclassified:")
        for m in accuracy["misses"][:10]:
            print(f"    - {m}")
        if len(accuracy["misses"]) > 10:
            print(f"    ... and {len(accuracy['misses']) - 10} more")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the recovery loop over a synthetic batch")
    ap.add_argument("--cases", default="synthetic_cases.json")
    ap.add_argument("--db", default=None, help="database path (default: DB_PATH / recovery.db)")
    ap.add_argument("--live-links", type=int, default=0,
                    help="cap on REAL Razorpay test-mode Payment Links to create (default 0)")
    ap.add_argument("--seed", type=int, default=None, help="override the outcome-model seed")
    ap.add_argument("--holdout", type=float, default=0.0, metavar="FRACTION",
                    help="fraction of cases held out as an untreated control arm, so recovery "
                         "can be reported as incremental rather than gross (default 0.0 = no "
                         "control arm, which reproduces the frozen batch exactly)")
    ap.add_argument("--keep", action="store_true", help="append to an existing database instead of resetting it")
    args = ap.parse_args()

    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if isinstance(data, list):  # tolerate a bare array of cases
        data = {"meta": {}, "cases": data}
    seed = args.seed if args.seed is not None else data.get("meta", {}).get("seed", 42)

    path = args.db or config.DB_PATH
    db.init(path) if args.keep else db.reset(path)

    start = clock.parse_iso(data["meta"].get("batch_start", "2026-03-02T09:00:00Z"))
    clock.set_clock(clock.SimulatedClock(start))

    db.insert("batch_run", {
        "seed": seed, "n_cases": len(data["cases"]),
        "started_at": clock.now_iso(), "finished_at": None, "summary": None,
    })

    problem = llm.misconfiguration()
    if problem:
        print(f"\nWARNING: {problem}\n"
              "         Every ambiguous case will stop as `unknown` and this run will still\n"
              "         exit 0 with green checks. Run `python scripts/check_llm.py` first.\n")

    if not 0.0 <= args.holdout < 1.0:
        print("--holdout must be in [0.0, 1.0)")
        return 2
    runner = Runner(data, random.Random(seed), args.live_links, args.holdout)
    print(f"running {len(data['cases'])} synthetic cases on a simulated clock from "
          f"{clock.now_iso()} (seed {seed}, live links {args.live_links}, "
          f"LLM {config.LLM_PROVIDER})")
    runner.run()

    accuracy = classification_accuracy(runner)
    print_summary(runner, accuracy)

    print("\nACCEPTANCE CHECKS")
    failures = 0
    for name, ok, detail in acceptance_checks():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if not ok and detail else ""))
        failures += 0 if ok else 1

    run_id = db.scalar("SELECT MAX(id) FROM batch_run")
    db.update("batch_run", run_id, {
        "finished_at": clock.now_iso(),
        "summary": json.dumps({"metrics": metrics.summary(), "accuracy": accuracy}, default=str),
    })

    if failures:
        print(f"\n{failures} acceptance check(s) FAILED")
        return 1
    print("\nall acceptance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
