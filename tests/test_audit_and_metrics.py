"""Audit-trail discipline and metric traceability (docs/07)."""
from __future__ import annotations

import re
from pathlib import Path

from app import audit, cases, db, executor, metrics, webhooks
from tests.conftest import failure_payload, recovery_payload

APP = Path(__file__).resolve().parent.parent / "app"


def test_no_update_or_delete_against_the_audit_log_anywhere():
    pattern = re.compile(r"(UPDATE\s+audit_log|DELETE\s+FROM\s+audit_log)", re.I)
    offenders = [p.name for p in APP.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_audit_seq_is_gapless_and_starts_at_one(fresh_db):
    webhooks.intake(failure_payload(), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    seqs = [e["seq"] for e in audit.trail(case["id"])]
    assert seqs == list(range(1, len(seqs) + 1))


def test_every_stage_of_a_full_lifecycle_is_audited(fresh_db, sim_clock):
    webhooks.intake(failure_payload(), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    sim_clock.advance(hours=25)
    executor.tick()
    webhooks.intake(recovery_payload(case_id=case["id"]), source="synthetic")

    stages = [e["stage"] for e in audit.trail(case["id"])]
    for expected in ("detect", "diagnose", "decide", "execute", "outcome"):
        assert expected in stages, (expected, stages)
    assert stages[-1] == "outcome"


def test_every_decision_cites_its_policy_cell_and_carries_receipts(fresh_db):
    webhooks.intake(failure_payload(), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    for row in db.query("SELECT * FROM intervention_decision WHERE case_id = ?", (case["id"],)):
        assert row["policy_row_ref"]
        import json
        checks = json.loads(row["invariant_check"])
        assert "pre_decision" in checks
        assert set(checks["pre_decision"]) >= {"I1", "I2", "I3", "I4"}


def test_a_stop_names_the_invariant_not_the_policy(fresh_db):
    webhooks.intake(failure_payload(opted_out=True), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    last = audit.trail(case["id"])[-1]
    assert last["stage"] == "stop"
    assert last["detail"]["invariant"] == "I3"
    assert "invariant_rule" in last["detail"]


def test_metrics_return_the_case_ids_behind_every_value(fresh_db):
    webhooks.intake(failure_payload(), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    webhooks.intake(recovery_payload(case_id=case["id"]), source="synthetic")

    value, ids = metrics.recovered()
    assert ids == [case["id"]]
    assert value == cases.get(case["id"])["amount_at_risk_paise"]

    traced = metrics.trace("recovered")
    assert traced["case_ids"] == ids and traced["value"] == value
    assert metrics.trace("nonsense_metric") is None


def test_reconciliation_identity_holds(fresh_db, sim_clock):
    for i in range(4):
        webhooks.intake(failure_payload(event_id=f"e{i}", sub_id=f"sub{i}", cust_id=f"cust{i}",
                                        pay_id=f"pay{i}"), source="synthetic")
    sim_clock.advance(hours=30)
    executor.tick()
    rec = metrics.reconciliation()
    assert rec["counts_balance"] and rec["amounts_balance"]
    assert rec["n_cases"] == 4


def test_recovery_rate_denominator_excludes_open_cases(fresh_db):
    webhooks.intake(failure_payload(event_id="e1", sub_id="s1", cust_id="c1"), source="synthetic")
    webhooks.intake(failure_payload(event_id="e2", sub_id="s2", cust_id="c2", error=None),
                    source="synthetic")   # -> stopped_unknown, i.e. closed
    rate = metrics.recovery_rate()
    assert rate["denominator"] == 1        # only the stopped case is closed
    assert rate["strict_denominator"] == 2
    assert rate["n_open"] == 1


def test_money_is_paise_integers_everywhere(fresh_db):
    webhooks.intake(failure_payload(amount=49900), source="synthetic")
    row = db.query_one("SELECT amount_at_risk_paise FROM recovery_case")
    assert isinstance(row["amount_at_risk_paise"], int)
    assert cases.rupees(row["amount_at_risk_paise"]) == 499.0
