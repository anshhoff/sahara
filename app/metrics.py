"""Metrics — every headline number, and the case ids behind it (docs/07).

Discipline: never report a number you cannot trace to specific case ids. Each metric
is implemented as *select the ids, then aggregate*, and returns both. There is
structurally no way to compute the headline number without materialising its member
set, which is what `GET /api/metrics/trace/{metric}` serves.

Nothing here is a counter. Every value is a query over the base tables, so the
database is the single source of truth and the dashboard cannot drift from it.
"""
from __future__ import annotations

import json
import random
from typing import Any, Optional

from app import audit, clock, config, db, fencing

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
    """Sum the amount at risk over exactly these case ids.

    The ids are staged in a temp table and joined, rather than expanded into an
    `IN (?, ?, …)` list. An id list is one bind parameter each, and SQLite caps a
    statement's parameters (999 on older builds), so the obvious version silently
    works at demo scale and then fails on the first merchant with a thousand open
    cases. A join has no such ceiling, and every metric here is defined as
    "the total behind this id list" — so the traceability contract is unchanged.
    """
    if not case_ids:
        return 0
    db.execute("CREATE TEMP TABLE IF NOT EXISTS _traced_ids (id TEXT PRIMARY KEY)")
    db.execute("DELETE FROM _traced_ids")
    db.executemany("INSERT OR IGNORE INTO _traced_ids (id) VALUES (?)", ((i,) for i in case_ids))
    return int(db.scalar(
        "SELECT COALESCE(SUM(c.amount_at_risk_paise), 0) FROM recovery_case c"
        " JOIN _traced_ids t ON t.id = c.id",
        (), 0,
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
    # `plivo_trial_verified` is listed even at zero, on purpose. "Did this system ever
    # actually dial a real person?" deserves a visible answer rather than a missing key.
    out = {"razorpay_test": 0, "simulated": 0, "plivo_trial_verified": 0}
    out.update({r["mode"]: int(r["n"]) for r in rows})
    return out


def executions_by_action() -> dict[str, int]:
    """Voice calls counted distinctly from link sends — the acceptance criterion for
    registering VOICE_CALL as an action rather than a channel."""
    out = {a: 0 for a in config.ACTIONS}
    out.update({r["action"]: int(r["n"]) for r in db.query(
        "SELECT action, COUNT(*) AS n FROM execution_record GROUP BY action")})
    return out


# -------------------------------------------------------------------- promises
def promises() -> dict[str, Any]:
    """Dated promises the customer actually made, and whether they held.

    Traceable like everything else: the case ids behind each count are returned, so
    `promises_kept` can be walked back to the audit entry that recorded the date and
    the outcome entry that settled it.

    `promises_kept` is not a recovery metric. A kept promise is a case that recovered,
    and it is already counted there; this measures whether the READING was worth
    anything — whether scheduling to a date a model extracted from Hinglish speech beats
    doing nothing with it.
    """
    rows = db.rows_to_dicts(db.query(
        "SELECT id, case_id, status, promised_date, due_at, source, reading_confidence"
        " FROM promise ORDER BY created_at"))
    by_status: dict[str, list[str]] = {"open": [], "kept": [], "broken": []}
    by_source: dict[str, dict[str, int]] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r["case_id"])
        bucket = by_source.setdefault(r["source"], {"open": 0, "kept": 0, "broken": 0})
        bucket[r["status"]] = bucket.get(r["status"], 0) + 1
    resolved = len(by_status["kept"]) + len(by_status["broken"])
    return {
        "n_promises": len(rows),
        "promises_kept": len(by_status["kept"]),
        "promises_broken": len(by_status["broken"]),
        "promises_open": len(by_status["open"]),
        "kept_rate": round(len(by_status["kept"]) / resolved, 4) if resolved else None,
        "case_ids": {k: v for k, v in by_status.items()},
        "by_reading_source": by_source,
        "note": ("a promise is a date the customer NAMED, not a window we imposed. There is "
                 "no amount on a promise and there cannot be one — the inbound schema has "
                 "no field for a figure, so a compromised model has nowhere to put one."),
    }


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


# ------------------------------------------------------------------ economics
def costs() -> dict[str, Any]:
    """What the recovery cost to run, in rupees that actually moved.

    Two components, kept apart because they are spent on different things:

    * **outreach** — the sum of `execution_record.cost_paise`, every message the agent
      sent, successful or not.
    * **handoff** — the human queue. A case is not free to give to a person, and a
      system that could make any hard case disappear at zero cost by handing it off
      would be measuring the wrong thing. `config.HANDOFF_STATUSES` says exactly which
      terminal states count, and why the others do not.

    The annoyance term from `economics.evaluate` is deliberately absent. It prices a
    decision before it is taken, but no rupee ever leaves the account for it, and
    booking a modelled risk as a realised cost would make this ledger an opinion.
    """
    outreach = int(db.scalar("SELECT COALESCE(SUM(cost_paise), 0) FROM execution_record", (), 0))
    marks = ", ".join("?" for _ in config.HANDOFF_STATUSES)
    n_handoff = int(db.scalar(
        f"SELECT COUNT(*) FROM recovery_case WHERE status IN ({marks})",
        config.HANDOFF_STATUSES, 0))
    handoff = n_handoff * int(config.ACTION_COST_PAISE["STOP_HANDOFF"])
    by_action = {
        r["action"]: {"n": int(r["n"]), "paise": int(r["paise"] or 0)}
        for r in db.query(
            "SELECT action, COUNT(*) AS n, COALESCE(SUM(cost_paise), 0) AS paise"
            " FROM execution_record GROUP BY action")
    }
    return {
        "outreach_paise": outreach,
        "n_handoff_cases": n_handoff,
        "handoff_paise": handoff,
        "total_paise": outreach + handoff,
        "by_action": by_action,
        "unit_costs_paise": dict(config.ACTION_COST_PAISE),
    }


def net_recovery() -> dict[str, Any]:
    """Recovered minus spent — gross, and then the version that survives its own costs.

    `net_incremental_paise` is the number this project actually stands behind: money
    that came back BECAUSE of the agent (treated minus control), minus everything the
    agent spent to get it. Gross recovery cannot go down by sending more messages,
    which is what makes it the wrong headline and this the right one.
    """
    recovered_paise, _ = recovered()
    c = costs()
    inc = incremental_recovery()
    gross_net = recovered_paise - c["total_paise"]
    out = {
        "gross_recovered_paise": recovered_paise,
        "total_cost_paise": c["total_paise"],
        "net_recovered_paise": gross_net,
        # Rupees spent per Rs 100 that came back. Directly comparable across arms,
        # batches and merchants in a way that a raw total is not.
        "cost_per_100_recovered": (round(c["total_paise"] / recovered_paise * 100, 2)
                                   if recovered_paise else None),
        "incremental_available": bool(inc.get("available")),
    }
    if inc.get("available"):
        # Every rupee of cost was spent on the treated arm — a control case is never
        # executed against — so the whole cost is subtracted from the incremental
        # figure rather than apportioned between the arms.
        out["incremental_paise"] = inc["incremental_paise_total"]
        out["net_incremental_paise"] = inc["incremental_paise_total"] - c["total_paise"]
        out["incremental_paise_ci95"] = inc["incremental_paise_ci95"]
        out["net_incremental_paise_ci95"] = [
            inc["incremental_paise_ci95"][0] - c["total_paise"],
            inc["incremental_paise_ci95"][1] - c["total_paise"],
        ]
    return out


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
def self_cure_recovered_ids() -> Optional[set[str]]:
    """Cases whose recovery signal arrived from the WORLD rather than from us.

    On a synthetic batch, `scripts/run_batch.py` stamps every recovery event it emits
    with `simulated_origin`, and that stamp lands in `failure_event.raw_payload`, which
    is immutable after insert. A recovery carrying `self_cure` is one the customer's
    payday or the issuer produced; anything else followed an intervention.

    This distinction cannot be recovered from the case row. Once a treated case is
    `recovered`, nothing about it says whether the agent earned that rupee — which is
    exactly why the control arm exists, and why the per-arm organic counts are worth
    publishing next to the lift rather than asked to be taken on trust.

    Returns None when no event carries the stamp at all — a live database, or a batch
    written before it existed. None means "not knowable here", and every caller reports
    it as unavailable rather than as zero. A missing measurement is not a measurement
    of zero.
    """
    rows = db.query(
        "SELECT fe.case_id AS case_id, fe.raw_payload AS raw_payload FROM failure_event fe"
        " WHERE fe.case_id IS NOT NULL AND fe.event_type IN ('subscription.charged','payment_link.paid')")
    ids: set[str] = set()
    stamped = False
    for r in rows:
        try:
            origin = (json.loads(r["raw_payload"]) or {}).get("simulated_origin")
        except (TypeError, ValueError):
            continue
        if origin is None:
            continue
        stamped = True
        if origin == "self_cure":
            ids.add(r["case_id"])
    return ids if stamped else None


def _arm(holdout: int) -> list[dict[str, Any]]:
    """One arm's cases, each carrying how many interventions were executed against it."""
    return db.rows_to_dicts(db.query(
        "SELECT c.id AS id, c.status AS status, c.amount_at_risk_paise AS amount_at_risk_paise,"
        " (SELECT COUNT(*) FROM execution_record e WHERE e.case_id = c.id) AS n_executions"
        " FROM recovery_case c WHERE c.is_holdout = ? ORDER BY c.created_at", (holdout,)))


def incremental_recovery(bootstrap: int = 10000, seed: int = 42) -> dict[str, Any]:
    """Recovery attributable to the agent, not merely observed alongside it.

    Gross recovery answers "how much came back?". It cannot answer "how much came back
    *because of us?*" — some failed charges recover on their own when the customer tops
    up or the issuer stops declining. Without a control arm those rupees are silently
    credited to the agent.

    So a randomised subset of cases is held out: detected, diagnosed, then never
    intervened on. The difference in recovery rate between the arms is the agent's
    effect; everything else is what would have happened anyway.

    Two properties worth stating plainly:

    * **Intention-to-treat.** Every case counts in the arm it was assigned to, whatever
      status it reached. A treated case stopped by an invariant stays in the treated
      arm — dropping the ones the agent refused to act on would flatter the result by
      exactly the cases it handled most conservatively.
    * **The interval is sampling uncertainty only.** It quantifies how much of the gap
      could be chance given this many cases. It says nothing about whether the
      underlying outcome model is right; on a synthetic batch that model is an
      assumption, so this is a measurement of the *mechanism*, not of the market.

    Returns rates in [0, 1] and money in paise, with a percentile bootstrap CI over
    cases. Deterministic for a given seed.
    """
    treated, control = _arm(0), _arm(1)
    if not control:
        return {"available": False,
                "reason": "no control arm in this batch — run with --holdout to create one"}

    def rate(rows: list[dict[str, Any]]) -> float:
        return sum(1 for r in rows if r["status"] == "recovered") / len(rows) if rows else 0.0

    def money_per_case(rows: list[dict[str, Any]]) -> float:
        """Recovered rupees divided by cases *assigned* to the arm — not by cases that
        recovered. Per-assigned-case is what makes the two arms subtractable, and it
        keeps the money figure bounded by gross: the control arm can only ever reduce
        what the agent is credited with."""
        if not rows:
            return 0.0
        return sum(int(r["amount_at_risk_paise"]) for r in rows if r["status"] == "recovered") / len(rows)

    t_rate, c_rate = rate(treated), rate(control)
    lift = t_rate - c_rate
    money_lift = money_per_case(treated) - money_per_case(control)

    # One resample drives both statistics, so the rate interval and the money interval
    # describe the same simulated batches rather than two unrelated ones.
    rng = random.Random(seed)
    rate_diffs: list[float] = []
    money_diffs: list[float] = []
    for _ in range(bootstrap):
        rt = [treated[rng.randrange(len(treated))] for _ in range(len(treated))]
        rc = [control[rng.randrange(len(control))] for _ in range(len(control))]
        rate_diffs.append(rate(rt) - rate(rc))
        money_diffs.append(money_per_case(rt) - money_per_case(rc))

    def ci(xs: list[float]) -> tuple[float, float]:
        xs = sorted(xs)
        return xs[int(0.025 * len(xs))], xs[min(int(0.975 * len(xs)), len(xs) - 1)]

    lo, hi = ci(rate_diffs)
    m_lo, m_hi = ci(money_diffs)
    gross = sum(int(r["amount_at_risk_paise"]) for r in treated if r["status"] == "recovered")

    # Organic = recovered on a self-cure signal, i.e. money that would have arrived
    # with or without the agent. Self-cure is a property of the world, so it must run
    # at about the same rate in both arms; publishing both counts is what lets a
    # reader check the arms are balanced instead of taking it on faith.
    self_cured = self_cure_recovered_ids()

    def organic(rows: list[dict[str, Any]]) -> Optional[int]:
        if self_cured is None:
            return None
        return sum(1 for r in rows if r["status"] == "recovered" and r["id"] in self_cured)

    def untouched(rows: list[dict[str, Any]]) -> int:
        """Recovered without a single intervention having been executed. Distinct from
        organic: a treated case can self-cure *after* we contacted it, in which case it
        is organic but not untouched."""
        return sum(1 for r in rows if r["status"] == "recovered" and int(r["n_executions"]) == 0)

    def arm_block(rows: list[dict[str, Any]], r: float) -> dict[str, Any]:
        n_org = organic(rows)
        return {
            "n": len(rows),
            "recovered": sum(1 for x in rows if x["status"] == "recovered"),
            "rate": round(r, 4),
            "organic": n_org,
            "organic_rate": (None if n_org is None or not rows else round(n_org / len(rows), 4)),
            "untouched": untouched(rows),
        }

    org_t, org_c = organic(treated), organic(control)

    return {
        "available": True,
        "treated": arm_block(treated, t_rate),
        "control": arm_block(control, c_rate),
        # Stated as its own field so the dashboard and the README can cite the balance
        # claim without recomputing it.
        "organic_balance": {
            "available": self_cured is not None,
            "treated": org_t,
            "control": org_c,
            "treated_rate": (None if org_t is None or not treated else round(org_t / len(treated), 4)),
            "control_rate": (None if org_c is None or not control else round(org_c / len(control), 4)),
            "note": ("organic = recovered on a self-cure signal rather than after an "
                     "intervention. Self-cure is a property of the world, so it is rolled "
                     "for every case in BOTH arms from the same distribution and should "
                     "run at about the same rate in each. Derivable only on a synthetic "
                     "batch, where the simulator stamps the provenance of every recovery "
                     "event it emits."),
        },
        "lift": round(lift, 4),
        "lift_ci95": [round(lo, 4), round(hi, 4)],
        # Significant only when the interval excludes zero — i.e. the sign of the
        # effect is not in doubt at this sample size.
        "significant": lo > 0 or hi < 0,
        "incremental_paise_per_treated_case": int(round(money_lift)),
        "incremental_paise_total": int(round(money_lift * len(treated))),
        "incremental_paise_ci95": [int(round(m_lo * len(treated))), int(round(m_hi * len(treated)))],
        "gross_recovered_paise": gross,
        "bootstrap_samples": bootstrap,
        "basis": ("outcome probabilities are modelling assumptions, not measured market "
                  "data; this measures the mechanism, not the market"),
    }


# --------------------------------------------------- 4.1 per-category lift
def lift_by_category(bootstrap: int = 4000, seed: int = 42) -> list[dict[str, Any]]:
    """Lift, with an interval, split by failure category — INCLUDING the categories
    where the agent does nothing.

    Publishing where it is flat is what makes the rest believable. A table in which
    every row is a win is a table nobody should believe, and the rows below where the
    interval spans zero are doing more work for the reader than the ones where it does
    not.

    Arm counts are shown on every row. Some categories carry twenty cases and the
    interval says so; a per-category number quoted without its n is a number designed
    to be misread.
    """
    rows = db.rows_to_dicts(db.query(
        "SELECT COALESCE(current_category, 'unknown') AS category, status, is_holdout,"
        " amount_at_risk_paise FROM recovery_case ORDER BY created_at"))
    if not any(int(r["is_holdout"]) for r in rows):
        return []

    def rate(xs: list[dict[str, Any]]) -> float:
        return sum(1 for x in xs if x["status"] == "recovered") / len(xs) if xs else 0.0

    out: list[dict[str, Any]] = []
    for category in config.CATEGORIES:
        treated = [r for r in rows if r["category"] == category and not int(r["is_holdout"])]
        control = [r for r in rows if r["category"] == category and int(r["is_holdout"])]
        entry: dict[str, Any] = {
            "category": category,
            "treated": {"n": len(treated),
                        "recovered": sum(1 for r in treated if r["status"] == "recovered"),
                        "rate": round(rate(treated), 4)},
            "control": {"n": len(control),
                        "recovered": sum(1 for r in control if r["status"] == "recovered"),
                        "rate": round(rate(control), 4)},
        }
        if not treated or not control:
            # Not "no effect" — no measurement. Reported as unavailable, because a
            # category with an empty arm has nothing to say and saying zero would be a
            # claim it cannot support.
            entry.update({"lift": None, "lift_ci95": None, "significant": None,
                          "reason": "one arm is empty at this sample size"})
            out.append(entry)
            continue

        lift = rate(treated) - rate(control)
        rng = random.Random(seed)
        diffs = []
        for _ in range(bootstrap):
            rt = [treated[rng.randrange(len(treated))] for _ in range(len(treated))]
            rc = [control[rng.randrange(len(control))] for _ in range(len(control))]
            diffs.append(rate(rt) - rate(rc))
        diffs.sort()
        lo = diffs[int(0.025 * len(diffs))]
        hi = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)]
        entry.update({
            "lift": round(lift, 4),
            "lift_ci95": [round(lo, 4), round(hi, 4)],
            "significant": lo > 0 or hi < 0,
        })
        out.append(entry)
    return out


# ------------------------------------------------- 4.2 prior calibration
def _realised_outcomes() -> list[dict[str, Any]]:
    """One row per executed intervention on a CLOSED case: the prior that justified it,
    and whether the money arrived after it.

    The attribution rule is *last-touch*: an intervention scores 1 only if it was the
    last thing done to the case AND the case recovered. Everything else scores 0 — an
    intervention followed by another intervention demonstrably did not work, which is
    exactly what the next one being necessary means.

    **The first version of this scored only the last execution and threw the rest away.
    That is a selection bias with a direction**: the last execution before a recovery is
    by construction the one that worked, so every prior came back "pessimistic" with a
    realised rate of 1.000. Keeping the successes and discarding the failures is not an
    attribution rule, it is a way of proving whatever you like.

    What last-touch still cannot do is split credit for a recovery across the three
    interventions that preceded it. That needs a model of how they combine, and
    inventing one would make this table an opinion — so the first two are scored 0 and
    the rule is stated rather than hidden. It reads as slightly harsh on early attempts,
    which is the safe direction for a table whose job is to catch over-confidence.
    """
    rows = db.rows_to_dicts(db.query(
        "SELECT e.id AS execution_id, e.case_id AS case_id, e.action AS action,"
        "       e.executed_at AS executed_at, d.attempt_number AS attempt,"
        "       c.status AS status, c.current_category AS category"
        " FROM execution_record e"
        " JOIN intervention_decision d ON d.id = e.decision_id"
        " JOIN recovery_case c ON c.id = e.case_id"
        " WHERE c.status != 'open'"
        " ORDER BY e.case_id, e.executed_at, e.rowid"))
    last_execution: dict[str, str] = {}
    for r in rows:
        last_execution[r["case_id"]] = r["execution_id"]

    out = []
    for r in rows:
        category = r["category"] or "unknown"
        worked = (r["status"] == "recovered"
                  and last_execution[r["case_id"]] == r["execution_id"])
        out.append({
            "execution_id": r["execution_id"],
            "case_id": r["case_id"],
            "category": category,
            "action": r["action"],
            "attempt": int(r["attempt"]),
            "prior": _prior(category, r["action"], int(r["attempt"])),
            "recovered": 1 if worked else 0,
        })
    return out


def _prior(category: str, action: str, attempt: int) -> float:
    row = config.P_RECOVER_PRIOR.get((category, action), config.P_RECOVER_DEFAULT)
    return float(row[max(1, min(attempt, config.MAX_ATTEMPTS)) - 1])


def prior_calibration(n_bins: int = 5) -> dict[str, Any]:
    """Score `P_RECOVER_PRIOR` against what actually happened.

    These priors drive every EV gate in the system — they decide which interventions
    fire and which cases are handed to a person — and until now they had never been
    checked against a single outcome. A number that decides where money goes and has
    never been scored is an assumption wearing a measurement's clothes.

    Brier score is mean squared error on the probabilities: 0 is perfect, 0.25 is what
    you get by always saying 0.5, and lower is better. ECE is the average gap between
    what the prior claimed and what happened, weighted by how often each bin came up —
    Brier punishes confident errors, ECE says which direction the errors run.
    """
    rows = _realised_outcomes()
    if not rows:
        return {"available": False,
                "reason": "no closed case has an executed intervention to score"}

    brier = sum((r["prior"] - r["recovered"]) ** 2 for r in rows) / len(rows)

    bins: list[dict[str, Any]] = []
    ece = 0.0
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        members = [r for r in rows
                   if (lo <= r["prior"] < hi) or (i == n_bins - 1 and r["prior"] == hi)]
        if not members:
            bins.append({"bin": f"[{lo:.1f}, {hi:.1f})", "n": 0,
                         "mean_prior": None, "realised": None, "gap": None})
            continue
        mean_prior = sum(r["prior"] for r in members) / len(members)
        realised = sum(r["recovered"] for r in members) / len(members)
        ece += (len(members) / len(rows)) * abs(mean_prior - realised)
        bins.append({
            "bin": f"[{lo:.1f}, {hi:.1f})",
            "n": len(members),
            "mean_prior": round(mean_prior, 4),
            "realised": round(realised, 4),
            "gap": round(realised - mean_prior, 4),
        })

    pairs: list[dict[str, Any]] = []
    keys = sorted({(r["category"], r["action"]) for r in rows})
    for category, action in keys:
        members = [r for r in rows if r["category"] == category and r["action"] == action]
        mean_prior = sum(r["prior"] for r in members) / len(members)
        realised = sum(r["recovered"] for r in members) / len(members)
        pairs.append({
            "category": category,
            "action": action,
            "n": len(members),
            "prior": round(mean_prior, 4),
            "realised": round(realised, 4),
            "gap": round(realised - mean_prior, 4),
            "direction": ("optimistic" if mean_prior > realised else
                          "pessimistic" if mean_prior < realised else "exact"),
        })
    pairs.sort(key=lambda p: -abs(p["gap"]))

    return {
        "available": True,
        "n_scored": len(rows),
        "n_executions_total": int(db.scalar("SELECT COUNT(*) FROM execution_record", (), 0)),
        "brier_score": round(brier, 4),
        "ece": round(ece, 4),
        "reliability": bins,
        "by_pair": pairs,
        "attribution": ("last-touch: an intervention scores 1 only if it was the last thing "
                        "done to the case AND the case recovered. An intervention followed by "
                        "another one demonstrably did not work. Credit cannot be split across "
                        "a sequence without a model of how they combine, so early attempts in "
                        "a recovered sequence score 0 — harsh, and the safe direction for a "
                        "table whose job is to catch over-confidence. Scoring only the last "
                        "execution instead would keep every success and discard every failure, "
                        "which is not an attribution rule."),
        "reference": ("Brier 0.25 is what always answering 0.5 scores; the base rate's own "
                      "Brier is the number to beat, not zero"),
    }


# ---------------------------------------------- 4.3 what the agent declined to do
# Terminal states in which the agent deliberately did NOT contact the customer, and
# the reason for each. This is promoted out of stopped_by_status() and into the
# headline because it is the strongest thing this system has to say: what it declined
# to do is a harder claim than what it achieved, and nothing else in the field reports
# it at all.
_DECLINED_REASONS: dict[str, str] = {
    "stopped_opt_out": "the customer had opted out (I3)",
    "stopped_suppressed": "the customer is on the suppression list (I6)",
    "stopped_unknown": "the failure cause was unknown and the agent does not guess (I4)",
    "stopped_uneconomic": "the contact would have cost more than it was likely to return (E1)",
    "stopped_already_settled": "the money had already arrived between deciding and acting (fencing)",
    "stopped_unverified_recipient": "the destination was not on the verified allowlist (I8)",
    "stopped_holdout": "the case was assigned to the control arm and deliberately never worked",
}


def declined_to_contact() -> dict[str, Any]:
    """Cases the agent deliberately did not contact, with the reason for each."""
    by_status = stopped_by_status()
    rows = [{"status": status, "n": by_status.get(status, 0), "why": why}
            for status, why in _DECLINED_REASONS.items()]
    # The control arm is separated out: those cases were withheld to measure the rest,
    # not because a rule said no. Folding them in would inflate the restraint figure
    # with an experimental design choice.
    guarded = [r for r in rows if r["status"] != "stopped_holdout"]
    return {
        "n_declined": sum(r["n"] for r in guarded),
        "n_control_arm": by_status.get("stopped_holdout", 0),
        "by_reason": rows,
        "case_ids": _ids(
            "SELECT id FROM recovery_case WHERE status IN "
            "('stopped_opt_out','stopped_suppressed','stopped_unknown','stopped_uneconomic',"
            " 'stopped_already_settled','stopped_unverified_recipient') ORDER BY created_at"),
        "note": ("cases the agent could have messaged and chose not to. The control arm is "
                 "counted separately: those were withheld to measure the rest, not refused "
                 "by a rule."),
    }


# ------------------------------------------------------------ 4.5 stage latency
def stage_latency() -> dict[str, Any]:
    """Wall-clock p50/p95 per pipeline stage, in milliseconds.

    Wall clock, deliberately, even on a batch running against a simulated clock: the
    simulated clock measures the modelled world's calendar and this measures how long
    our code took, and reporting one as the other would be nonsense in both directions.
    """
    rows = db.query(
        "SELECT stage, COUNT(*) AS n, AVG(duration_ms) AS mean FROM stage_timing GROUP BY stage")
    out: dict[str, Any] = {}
    for r in rows:
        values = sorted(float(x["duration_ms"]) for x in db.query(
            "SELECT duration_ms FROM stage_timing WHERE stage = ?", (r["stage"],)))
        if not values:
            continue

        def pct(q: float) -> float:
            # Nearest-rank. With a handful of samples an interpolating percentile
            # invents a duration nothing actually took.
            return round(values[min(len(values) - 1, max(0, int(round(q * len(values))) - 1))], 3)

        out[r["stage"]] = {
            "n": int(r["n"]),
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "max_ms": round(values[-1], 3),
            "mean_ms": round(float(r["mean"]), 3),
        }
    return {
        "by_stage": out,
        "basis": ("wall clock, in milliseconds, even under a simulated clock — the simulated "
                  "clock measures the modelled world's calendar, this measures how long the "
                  "code took, and reporting either as the other would be nonsense"),
    }


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
        "executions_by_action": executions_by_action(),
        "promises": promises(),
        "reconciliation": reconciliation(),
        "incremental": incremental_recovery(),
        "lift_by_category": lift_by_category(),
        "calibration": prior_calibration(),
        "declined_to_contact": declined_to_contact(),
        "latency": stage_latency(),
        "fencing": fencing.fence_stats(),
        "costs": costs(),
        "net": net_recovery(),
        "audit_chain": audit.verify(),
        "bounds": {
            "max_attempts": config.MAX_ATTEMPTS,
            "cooldown_hours": config.COOLDOWN_HOURS,
            "episode_window_days": config.EPISODE_WINDOW_DAYS,
            "quiet_hours_ist": [config.QUIET_HOURS_START_IST, config.QUIET_HOURS_END_IST],
            "max_contacts_per_customer_per_day": config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY,
            "daily_outreach_budget_paise": config.DAILY_OUTREACH_BUDGET_PAISE,
            "llm_confidence_threshold": config.LLM_CONFIDENCE_THRESHOLD,
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": config.LLM_MODEL if config.LLM_PROVIDER != config.PROVIDER_NONE else None,
        },
    }


def promises_kept() -> Traced:
    ids = _ids("SELECT case_id AS id FROM promise WHERE status = 'kept' ORDER BY created_at")
    return len(ids), ids


def promises_broken() -> Traced:
    ids = _ids("SELECT case_id AS id FROM promise WHERE status = 'broken' ORDER BY created_at")
    return len(ids), ids


TRACEABLE = {
    "recovered": recovered,
    "at_risk": at_risk,
    "stopped": stopped,
    "promises_kept": promises_kept,
    "promises_broken": promises_broken,
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
