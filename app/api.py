"""Dashboard JSON API (docs/06 §2). All GET and read-only, except the single demo
helper that flips a customer's opt-out flag so invariant I3 can be shown live.

Every aggregate is a straight SQL query through metrics.py — no precomputed metric
rows, no cache. At roughly a hundred cases everything is instant, and the database
stays the only source of truth.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app import audit, cases, config, db, metrics

router = APIRouter(prefix="/api", tags=["dashboard"])


def _json_field(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "llm_provider": config.LLM_PROVIDER,
        "llm_copy_enabled": config.LLM_COPY_ENABLED,
        "razorpay_configured": bool(config.RAZORPAY_KEY_ID),
        "db_path": config.DB_PATH,
    }


@router.get("/summary")
def summary() -> dict[str, Any]:
    return metrics.summary()


@router.get("/categories")
def categories() -> list[dict[str, Any]]:
    return metrics.by_category()


@router.get("/cases")
def list_cases(status: Optional[str] = Query(None), category: Optional[str] = Query(None)) -> list[dict[str, Any]]:
    sql = ("SELECT id AS case_id, current_category AS category, amount_at_risk_paise, attempt_count,"
           " status, synthetic, customer_opted_out, subscription_id, created_at, updated_at, closed_at"
           " FROM recovery_case WHERE 1 = 1")
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if category:
        sql += " AND current_category = ?"
        params.append(category)
    sql += " ORDER BY updated_at DESC"
    rows = db.rows_to_dicts(db.query(sql, params))
    for r in rows:
        r["amount_rupees"] = cases.rupees(r["amount_at_risk_paise"])
    return rows


@router.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    """The handoff artifact: this response IS the case file a human picks up."""
    case = cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case {case_id}")
    case["amount_rupees"] = cases.rupees(case["amount_at_risk_paise"])

    events = db.rows_to_dicts(db.query(
        "SELECT * FROM failure_event WHERE case_id = ? ORDER BY rowid", (case_id,)))
    for e in events:
        e["raw_payload"] = _json_field(e["raw_payload"])

    diagnoses = db.rows_to_dicts(db.query(
        "SELECT * FROM diagnosis_result WHERE case_id = ? ORDER BY rowid", (case_id,)))

    decisions = db.rows_to_dicts(db.query(
        "SELECT * FROM intervention_decision WHERE case_id = ? ORDER BY rowid", (case_id,)))
    executions = db.rows_to_dicts(db.query(
        "SELECT * FROM execution_record WHERE case_id = ? ORDER BY rowid", (case_id,)))
    by_decision: dict[str, Any] = {}
    for x in executions:
        x["copy_validation"] = _json_field(x["copy_validation"])
        x["result_payload"] = _json_field(x["result_payload"])
        by_decision[x["decision_id"]] = x
    for d in decisions:
        d["invariant_check"] = _json_field(d["invariant_check"])
        d["execution"] = by_decision.get(d["id"])

    return {
        "case": case,
        "events": events,
        "diagnoses": diagnoses,
        "decisions": decisions,
        "audit_trail": audit.trail(case_id),
        "bounds": {
            "max_attempts": config.MAX_ATTEMPTS,
            "cooldown_hours": config.COOLDOWN_HOURS,
            "episode_window_days": config.EPISODE_WINDOW_DAYS,
        },
    }


@router.get("/metrics/trace/{metric}")
def trace(metric: str) -> dict[str, Any]:
    """Where a headline number comes from: the exact case ids behind it."""
    result = metrics.trace(metric)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown metric {metric}; try one of {sorted(metrics.TRACEABLE)}",
        )
    return result


@router.post("/cases/{case_id}/opt-out")
def opt_out(case_id: str) -> dict[str, Any]:
    """Demo helper for invariant I3: mark the customer opted out. The next gate —
    pre-decision or pre-execution, whichever comes first — stops the case."""
    case = cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case {case_id}")
    cases.set_opted_out(case_id, True)
    audit.audit(
        case_id, "detect", "human",
        "Customer opted out of recovery contact",
        {"source": "POST /api/cases/{id}/opt-out", "invariant": "I3"},
    )
    return {"case_id": case_id, "customer_opted_out": True, "status": cases.get(case_id)["status"]}
