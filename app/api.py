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

from app import audit, cases, config, db, invariants, metrics

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


# Plain-English gloss for each invariant. Kept next to the machine-readable rule text
# in invariants.INVARIANT_TEXT rather than in the dashboard, so the words a judge reads
# and the rule the code enforces cannot drift apart.
_INVARIANT_PLAIN = {
    "I1": ("Never more than a few tries",
           "After the cap, the case stops for good and goes to a human. There is no "
           "path — not a policy change, not a model output — that buys a fourth attempt."),
    "I2": ("Never two messages in a row",
           "A customer who was just contacted cannot be contacted again until the "
           "cooldown has passed. A due intervention waits; it does not fire early."),
    "I3": ("Opt-out beats everything",
           "Once someone opts out, nothing is sent and nothing is charged — checked "
           "again immediately before execution, so an opt-out that lands after the "
           "decision still stops it."),
    "I4": ("Never guess a cause",
           "If the failure reason is unknown — or the model was unsure, unavailable, or "
           "malformed — the case stops untouched rather than being acted on blindly."),
    "I5": ("Never message at night",
           "No customer contact leaves the system between 21:00 and 09:00 IST. A "
           "contact that comes due inside that window waits for the morning; it is "
           "never sent early and never dropped."),
    "I6": ("One opt-out covers the person",
           "A customer who asks to be left alone is left alone on every subscription "
           "they hold, including ones whose cases do not exist yet — and on silent "
           "re-charges too, not only on messages."),
    "I7": ("Never more than a few a day",
           "A customer has a daily contact ceiling across all of their cases, and the "
           "system as a whole has a daily outreach spend ceiling. Either one defers "
           "the contact to the next morning rather than dropping it."),
}

# Gate E1 is described here alongside the invariants because the dashboard shows them
# in one panel, but it is a different KIND of rule and the wording says so: the
# invariants are safety, and this is a business judgement made of estimates.
_ECONOMICS_PLAIN = (
    "Never send a message that costs more than it earns",
    "Every intervention is priced before it is taken — the chance it works times the "
    "money at stake, minus what it costs to send and the risk of pushing the customer "
    "into cancelling. A negative number stops the case. The arithmetic is written onto "
    "every decision, including the ones that went ahead.",
)


@router.get("/mechanism")
def mechanism() -> dict[str, Any]:
    """What the agent is, as data: the policy table it chooses from, the guardrails it
    cannot cross, and how many times each one actually fired in this batch.

    The dashboard renders this instead of hardcoding a copy of the rules. A gate that
    is described in one place and enforced in another eventually describes something
    the code no longer does.
    """
    stops = metrics.stopped_by_status()
    stop_for = {
        "I1": stops.get("stopped_max_attempts", 0),
        # Every timing gate that runs out of episode window closes the case under this
        # one status, so the count is attributed to the gate named in the audit entry
        # rather than assumed to be I2's.
        "I2": 0, "I5": 0, "I7": 0,
        "I3": stops.get("stopped_opt_out", 0),
        "I4": stops.get("stopped_unknown", 0),
        "I6": stops.get("stopped_suppressed", 0),
    }
    for code in ("I2", "I5", "I7"):
        stop_for[code] = int(db.scalar(
            "SELECT COUNT(*) FROM audit_log WHERE stage = 'stop' AND summary LIKE ?",
            (f"%by {code}:%",), 0))
    defers = {code: int(db.scalar(
        "SELECT COUNT(*) FROM audit_log WHERE stage = 'decide' AND summary LIKE ?",
        (f"%deferred by {code}:%",), 0)) for code in ("I2", "I5", "I7")}

    invariants_out = []
    for code in ("I1", "I2", "I3", "I4", "I5", "I6", "I7"):
        title, plain = _INVARIANT_PLAIN[code]
        invariants_out.append({
            "code": code,
            "title": title,
            "rule": invariants.INVARIANT_TEXT[code],
            "plain": plain,
            "kind": "safety" if code in ("I1", "I2", "I3", "I4") else "contact_hygiene",
            "stops": stop_for[code],
            "defers": defers.get(code, 0),
        })
    title, plain = _ECONOMICS_PLAIN
    invariants_out.append({
        "code": "E1",
        "title": title,
        "rule": "an intervention whose expected value is negative is never executed",
        "plain": plain,
        "kind": "economics",
        "stops": stops.get("stopped_uneconomic", 0),
        "defers": 0,
    })

    policy = [
        {"category": cat, "attempt": att,
         "action": config.POLICY[(cat, att)][0], "delay_hours": config.POLICY[(cat, att)][1]}
        for cat in config.CATEGORIES
        for att in range(1, config.MAX_ATTEMPTS + 1)
    ]

    rules = [
        {"id": rid, "category": cat, "patterns": list(pats)[:6], "n_patterns": len(pats),
         "n_matched": int(db.scalar(
             "SELECT COUNT(*) FROM diagnosis_result WHERE matched_rule = ?", (rid,), 0))}
        for rid, pats, cat in config.RULES
    ]
    rules.append({
        "id": config.RULE_R7, "category": "unknown", "patterns": [], "n_patterns": 0,
        "n_matched": int(db.scalar(
            "SELECT COUNT(*) FROM diagnosis_result WHERE matched_rule = ?", (config.RULE_R7,), 0)),
    })

    return {
        "funnel": {
            "events": int(db.scalar("SELECT COUNT(*) FROM failure_event", (), 0)),
            "cases": int(db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0)),
            "diagnoses": int(db.scalar("SELECT COUNT(*) FROM diagnosis_result", (), 0)),
            "decisions": int(db.scalar("SELECT COUNT(*) FROM intervention_decision", (), 0)),
            "blocked": int(db.scalar(
                "SELECT COUNT(*) FROM intervention_decision WHERE status = 'blocked_by_invariant'", (), 0)),
            "executions": int(db.scalar("SELECT COUNT(*) FROM execution_record", (), 0)),
            "contacts": int(db.scalar(
                "SELECT COUNT(*) FROM execution_record WHERE action IN"
                " ('SEND_UPDATE_LINK','PROMISE_TO_PAY')", (), 0)),
            "recovered": int(db.scalar(
                "SELECT COUNT(*) FROM recovery_case WHERE status = 'recovered'", (), 0)),
        },
        "invariants": invariants_out,
        "policy": policy,
        "rules": rules,
        "categories": list(config.CATEGORIES),
        "actions": sorted({p["action"] for p in policy}),
        "copy": {
            "slots": list(config.COPY_SLOTS),
            "max_chars": config.COPY_MAX_CHARS,
            "forbidden": list(config.COPY_FORBIDDEN),
            "disclosure": config.SYNTHETIC_DISCLOSURE,
        },
        "bounds": {
            "max_attempts": config.MAX_ATTEMPTS,
            "cooldown_hours": config.COOLDOWN_HOURS,
            "episode_window_days": config.EPISODE_WINDOW_DAYS,
            "promise_window_hours": config.PROMISE_WINDOW_HOURS,
            "quiet_hours_ist": [config.QUIET_HOURS_START_IST, config.QUIET_HOURS_END_IST],
            "max_contacts_per_customer_per_day": config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY,
            "daily_outreach_budget_paise": config.DAILY_OUTREACH_BUDGET_PAISE,
        },
        "economics": {
            "unit_costs_paise": dict(config.ACTION_COST_PAISE),
            "contact_churn_hazard": list(config.CONTACT_CHURN_HAZARD),
            "ltv_horizon_months": config.LTV_HORIZON_MONTHS,
            "p_recover_prior": [
                {"category": cat, "action": act, "by_attempt": list(row)}
                for (cat, act), row in sorted(config.P_RECOVER_PRIOR.items())
            ],
            "p_recover_default": list(config.P_RECOVER_DEFAULT),
            "basis": ("assumptions with stated rationale, not measurements; the priors are "
                      "deliberately not the batch simulator's outcome model"),
        },
    }


@router.get("/cases")
def list_cases(status: Optional[str] = Query(None), category: Optional[str] = Query(None)) -> list[dict[str, Any]]:
    sql = ("SELECT id AS case_id, current_category AS category, amount_at_risk_paise, attempt_count,"
           " status, synthetic, is_holdout, customer_opted_out, subscription_id, created_at, updated_at, closed_at"
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


@router.get("/audit/verify")
def audit_verify() -> dict[str, Any]:
    """Recompute the audit log's hash chain and report the first break, if any.

    This is the endpoint that makes "append-only" checkable by someone who does not
    trust the code: edit one summary in the database with sqlite3 and this turns red,
    naming the row. `head` is a one-line fingerprint of the whole run.
    """
    return audit.verify()


@router.get("/suppression")
def suppression_list() -> dict[str, Any]:
    """Who the agent will not contact, and why. Invariant I6 reads this table."""
    rows = cases.suppressed_customers()
    return {"n": len(rows), "rule": invariants.INVARIANT_TEXT["I6"], "customers": rows}


@router.post("/suppression/{customer_id}")
def suppress(customer_id: str, reason: str = Query("manual")) -> dict[str, Any]:
    """Add a customer to the suppression list.

    Deliberately has no counterpart that removes one. Un-suppressing someone is
    re-consenting on their behalf, which is not an operation this system should make
    one HTTP call away; it belongs in the database, deliberately, with a person
    accountable for it.
    """
    try:
        row = cases.suppress_customer(customer_id, reason, source="POST /api/suppression")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"customer_id": customer_id, "suppression": row, "invariant": "I6"}


@router.post("/cases/{case_id}/opt-out")
def opt_out(case_id: str) -> dict[str, Any]:
    """Demo helper for invariant I3: mark the customer opted out. The next gate —
    pre-decision or pre-execution, whichever comes first — stops the case."""
    case = cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case {case_id}")
    cases.set_opted_out(case_id, True)
    # An opt-out is a statement by a person, not about a subscription, so it lands in
    # both places: the case flag that I3 reads, and the suppression list that I6 reads
    # on every OTHER case that customer has, including ones that do not exist yet.
    suppression = cases.suppress_customer(
        case["customer_id"], "opt_out", source="POST /api/cases/{id}/opt-out",
        note=f"opted out on case {case_id}")
    audit.audit(
        case_id, "detect", "human",
        "Customer opted out of recovery contact, and was added to the suppression list",
        {"source": "POST /api/cases/{id}/opt-out", "invariant": "I3",
         "also": "I6", "customer_id": case["customer_id"]},
    )
    return {
        "case_id": case_id,
        "customer_opted_out": True,
        "status": cases.get(case_id)["status"],
        "suppression": suppression,
    }
