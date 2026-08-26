"""Metrics — every headline number, and the case ids behind it (docs/07).

Discipline: never report a number you cannot trace to specific case ids. Each metric
is implemented as *select the ids, then aggregate*, and returns both. There is
structurally no way to compute the headline number without materialising its member
set, which is what `GET /api/metrics/trace/{metric}` serves.

Nothing here is a counter. Every value is a query over the base tables, so the
database is the single source of truth and the dashboard cannot drift from it.
"""
from __future__ import annotations

from typing import Any, Optional

from app import clock, config, db

Traced = tuple[Any, list[str]]


# ------------------------------------------------------------------ base sets
def _ids(sql: str, params: tuple = ()) -> list[str]:
    return [r["id"] for r in db.query(sql, params)]


def eligible_cases() -> list[str]:
    return _ids("SELECT id FROM recovery_case ORDER BY created_at")


def closed_cases() -> list[str]:
    return _ids("SELECT id FROM recovery_case WHERE status != 'open' ORDER BY created_at")


def open_cases() -> list[str]:
    return _ids("SELECT id FROM recovery_case WHERE status = 'open' ORDER BY created_at")


def _sum_amount(case_ids: list[str]) -> int:
    if not case_ids:
        return 0
    marks = ",".join("?" for _ in case_ids)
    return int(db.scalar(
        f"SELECT COALESCE(SUM(amount_at_risk_paise), 0) FROM recovery_case WHERE id IN ({marks})",
        case_ids, 0,
    ))


# ------------------------------------------------------------------- headlines
def at_risk() -> Traced:
    """Rs at risk: one charge cycle per case, frozen at case creation. We do NOT
    multiply by the remaining subscription term — that would be projection, not
    measurement."""
    ids = eligible_cases()
    return _sum_amount(ids), ids


def recovered() -> Traced:
    """Rs recovered: only cases that received an actual recovery signal."""
    ids = _ids("SELECT id FROM recovery_case WHERE status = 'recovered' ORDER BY created_at")
    return _sum_amount(ids), ids


def stopped() -> Traced:
    ids = _ids(
        "SELECT id FROM recovery_case WHERE status LIKE 'stopped_%' ORDER BY created_at"
    )
    return len(ids), ids


def stopped_by_status() -> dict[str, int]:
    rows = db.query(
        "SELECT status, COUNT(*) AS n FROM recovery_case WHERE status LIKE 'stopped_%' GROUP BY status"
    )
    out = {s: 0 for s in config.STOPPED_STATUSES}
    out.update({r["status"]: int(r["n"]) for r in rows})
    return out


def recovery_rate() -> dict[str, Any]:
    """Denominator is CLOSED cases, so still-open cases cannot inflate the rate.
    The stricter recovered/eligible is reported alongside it, which preempts the
    denominator question rather than waiting for it."""
    _, rec_ids = recovered()
    closed = closed_cases()
    eligible = eligible_cases()
    return {
        "numerator": len(rec_ids),
        "denominator": len(closed),
        "rate": round(len(rec_ids) / len(closed), 4) if closed else 0.0,
        "strict_denominator": len(eligible),
        "strict_rate": round(len(rec_ids) / len(eligible), 4) if eligible else 0.0,
        "n_open": len(open_cases()),
    }


def avg_time_to_recovery_hours() -> Traced:
    rows = db.query(
        "SELECT id, created_at, closed_at FROM recovery_case"
        " WHERE status = 'recovered' AND closed_at IS NOT NULL"
    )
    if not rows:
        return None, []
    total, ids = 0.0, []
    for r in rows:
        delta = clock.parse_iso(r["closed_at"]) - clock.parse_iso(r["created_at"])
        total += delta.total_seconds() / 3600.0
        ids.append(r["id"])
    return round(total / len(ids), 2), ids


# ------------------------------------------------------------------- breakdown
def by_category() -> list[dict[str, Any]]:
    rows = db.query(
        """
        SELECT COALESCE(current_category, 'unknown') AS category,
               COUNT(*) AS n_cases,
               SUM(CASE WHEN status = 'recovered' THEN 1 ELSE 0 END) AS n_recovered,
               SUM(CASE WHEN status = 'recovered' THEN amount_at_risk_paise ELSE 0 END) AS recovered_paise,
               SUM(amount_at_risk_paise) AS at_risk_paise,
               SUM(CASE WHEN status LIKE 'stopped_%' THEN 1 ELSE 0 END) AS n_stopped,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS n_open
        FROM recovery_case GROUP BY COALESCE(current_category, 'unknown')
        """
    )
    out = []
    for r in rows:
        closed = int(r["n_cases"]) - int(r["n_open"])
        out.append({
            "category": r["category"],
            "n_cases": int(r["n_cases"]),
            "n_recovered": int(r["n_recovered"]),
            "n_stopped": int(r["n_stopped"]),
            "n_open": int(r["n_open"]),
            "recovery_rate": round(int(r["n_recovered"]) / closed, 4) if closed else 0.0,
            "at_risk_paise": int(r["at_risk_paise"] or 0),
            "recovered_paise": int(r["recovered_paise"] or 0),
        })
    order = {c: i for i, c in enumerate(config.CATEGORIES)}
    out.sort(key=lambda d: order.get(d["category"], 99))
    return out


# ------------------------------------------------------------- LLM involvement
def llm_involvement() -> dict[str, int]:
    return {
        "classified": int(db.scalar("SELECT COUNT(*) FROM diagnosis_result WHERE method = 'llm'", (), 0)),
        "classified_to_unknown": int(db.scalar(
            "SELECT COUNT(*) FROM diagnosis_result WHERE method = 'llm' AND category = 'unknown'", (), 0)),
        "rule_classified": int(db.scalar(
            "SELECT COUNT(*) FROM diagnosis_result WHERE method = 'rule'", (), 0)),
        "drafted": int(db.scalar(
            "SELECT COUNT(*) FROM execution_record WHERE copy_source = 'llm_draft'", (), 0)),
        "fallback_to_template": int(db.scalar(
            "SELECT COUNT(*) FROM execution_record WHERE copy_source = 'static_template'", (), 0)),
    }


def execution_modes() -> dict[str, int]:
    rows = db.query("SELECT mode, COUNT(*) AS n FROM execution_record GROUP BY mode")
    out = {"razorpay_test": 0, "simulated": 0}
    out.update({r["mode"]: int(r["n"]) for r in rows})
    return out


# ------------------------------------------------------------------ provenance
def synthetic_split() -> dict[str, Any]:
    n_total = int(db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0))
    n_synth = int(db.scalar("SELECT COUNT(*) FROM recovery_case WHERE synthetic = 1", (), 0))
    seed = db.scalar("SELECT seed FROM batch_run ORDER BY id DESC LIMIT 1")
    return {
        "n_cases": n_total,
        "n_synthetic": n_synth,
        "n_live": n_total - n_synth,
        "synthetic_share": round(n_synth / n_total, 4) if n_total else 0.0,
        "seed": seed,
    }


# ------------------------------------------------------------- reconciliation
def reconciliation() -> dict[str, Any]:
    """The identity asserted at the end of every batch run and checkable live
    (docs/07 §3.3)."""
    n_total = int(db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0))
    n_recovered = int(db.scalar("SELECT COUNT(*) FROM recovery_case WHERE status = 'recovered'", (), 0))
    n_open = len(open_cases())
    by_status = stopped_by_status()
    at_risk_paise, _ = at_risk()
    recovered_paise, _ = recovered()
    return {
        "n_cases": n_total,
        "n_recovered": n_recovered,
        "n_open": n_open,
        "n_stopped_total": sum(by_status.values()),
        "counts_balance": n_recovered + sum(by_status.values()) + n_open == n_total,
        "amounts_balance": recovered_paise <= at_risk_paise,
        "at_risk_paise": at_risk_paise,
        "recovered_paise": recovered_paise,
    }


# ------------------------------------------------------------------- summary
def summary() -> dict[str, Any]:
    at_risk_paise, _ = at_risk()
    recovered_paise, _ = recovered()
    ttr, _ = avg_time_to_recovery_hours()
    rate = recovery_rate()
    split = synthetic_split()
    by_status = stopped_by_status()
    return {
        **split,
        "total_at_risk_paise": at_risk_paise,
        "total_at_risk_rupees": round(at_risk_paise / 100, 2),
        "total_recovered_paise": recovered_paise,
        "total_recovered_rupees": round(recovered_paise / 100, 2),
        "recovery_rate": rate,
        "avg_time_to_recovery_hours": ttr,
        "time_basis": "simulated clock" if split["n_synthetic"] else "wall clock",
        "n_open": rate["n_open"],
        "n_recovered": rate["numerator"],
        "stopped": {"total": sum(by_status.values()), "by_status": by_status},
        "llm": llm_involvement(),
        "execution_modes": execution_modes(),
        "reconciliation": reconciliation(),
        "bounds": {
            "max_attempts": config.MAX_ATTEMPTS,
            "cooldown_hours": config.COOLDOWN_HOURS,
            "episode_window_days": config.EPISODE_WINDOW_DAYS,
            "llm_confidence_threshold": config.LLM_CONFIDENCE_THRESHOLD,
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": config.LLM_MODEL if config.LLM_PROVIDER != config.PROVIDER_NONE else None,
        },
    }


TRACEABLE = {
    "recovered": recovered,
    "at_risk": at_risk,
    "stopped": stopped,
}


def trace(metric: str) -> Optional[dict[str, Any]]:
    fn = TRACEABLE.get(metric)
    if fn is None:
        return None
    value, ids = fn()
    return {
        "metric": metric,
        "value": value,
        "value_rupees": round(value / 100, 2) if metric in {"recovered", "at_risk"} else None,
        "n_cases": len(ids),
        "case_ids": ids,
    }
