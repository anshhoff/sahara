"""Execute — the only place money-moving calls happen (docs/04 §6).

Nothing here is reachable from a model. The LLM contributes exactly one thing to this
module: a *draft string*, which validate_copy() checks deterministically before it is
used, and which is replaced by a static template if the check fails. Links are
injected by code after validation, so the model never sees or invents a URL.

Real Razorpay calls are test mode only, and Payment Links are created with
notifications explicitly disabled — Razorpay must never actually contact anyone.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app import audit, cases, clock, config, db, invariants, llm

log = logging.getLogger(__name__)

# Cap on real test-mode Payment Links per process (docs/02 §7 rate limits). The batch
# runner lowers or raises it with --live-links; everything above the cap is simulated
# and recorded honestly as such.
_live_link_budget = config.LIVE_LINKS_MAX
_razorpay_client: Any = None


def set_live_link_budget(n: int) -> None:
    global _live_link_budget
    _live_link_budget = max(0, int(n))


def live_link_budget() -> int:
    return _live_link_budget


def razorpay_client():
    global _razorpay_client
    if _razorpay_client is None:
        import razorpay

        _razorpay_client = razorpay.Client(
            auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)
        )
    return _razorpay_client


def razorpay_configured() -> bool:
    return bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)


# ------------------------------------------------------------------ copy stage
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _amount_str(case: dict[str, Any]) -> str:
    paise = int(case["amount_at_risk_paise"])
    rupees = paise / 100
    return str(int(rupees)) if rupees == int(rupees) else f"{rupees:.2f}"


_SLOT_RE = re.compile(r"\{[A-Za-z_]+\}")


def slot_values(case: dict[str, Any]) -> dict[str, str]:
    """Authoritative value for every slot except {LINK}, which is injected later —
    only once a real URL exists (docs/04 §6.3)."""
    return {
        config.AMOUNT_PLACEHOLDER: _amount_str(case),
        "{MERCHANT}": config.MERCHANT_NAME,
        "{COOLDOWN_HOURS}": str(config.COOLDOWN_HOURS),
        "{PROMISE_HOURS}": str(config.PROMISE_WINDOW_HOURS),
    }


def render_slots(text: str, case: dict[str, Any]) -> str:
    """Substitute every slot but {LINK}. Runs only after validate_copy has approved
    the skeleton, so no unvetted text can reach a customer."""
    for slot, value in slot_values(case).items():
        text = text.replace(slot, value)
    return text


def validate_copy(text: str, case: dict[str, Any]) -> dict[str, Any]:
    """Deterministic gate on a drafted copy *skeleton*. Every rule is checkable and
    each failure is stored verbatim on the ExecutionRecord (docs/04 §6.3).

    The central rule is that the draft may contain no digit at all. Numbers arrive
    only by slot substitution afterwards, so an invented figure — the one
    hallucination that would move real money if it reached a customer — is not
    merely detected, it is unrepresentable.
    """
    problems: list[str] = []
    t = text or ""

    if config.SYNTHETIC_DISCLOSURE not in t:
        problems.append(f"missing synthetic disclosure {config.SYNTHETIC_DISCLOSURE}")

    n_links = t.count(config.LINK_PLACEHOLDER)
    if n_links != 1:
        problems.append(f"{config.LINK_PLACEHOLDER} appears {n_links} times, expected exactly 1")
    if "http://" in t or "https://" in t:
        problems.append("draft contains a literal URL; links are injected by code only")

    unknown = sorted({s for s in _SLOT_RE.findall(t) if s not in config.COPY_SLOTS})
    if unknown:
        problems.append(f"unknown slots {unknown}; allowed: {sorted(config.COPY_SLOTS)}")

    # Digits are counted on the skeleton with its slots removed, so {AMOUNT} is fine
    # and a typed "499" is not — even when it happens to be the correct amount.
    skeleton = _SLOT_RE.sub("", t)
    literals = sorted({m.group(0) for m in _NUMBER_RE.finditer(skeleton)})
    if literals:
        problems.append(
            f"literal numbers {literals}; copy must use slots "
            f"({config.AMOUNT_PLACEHOLDER} etc.), never typed digits"
        )

    lowered = t.lower()
    hits = [w for w in config.COPY_FORBIDDEN if w in lowered]
    if hits:
        problems.append(f"forbidden words: {hits}")

    # Length is measured on what would actually be sent, not on the shorter skeleton.
    rendered = render_slots(t, case)
    if len(rendered) > config.COPY_MAX_CHARS:
        problems.append(f"rendered length {len(rendered)} exceeds {config.COPY_MAX_CHARS}")

    return {
        "ok": not problems,
        "problems": problems,
        "length": len(rendered),
        "amount_allowed": _amount_str(case),
    }


def static_template(category: str, action: str, case: dict[str, Any]) -> str:
    """The raw skeleton, slots intact. Templates and model drafts take the identical
    validate-then-render path, so the fallback is held to the same rules as the LLM."""
    return config.STATIC_TEMPLATES[(category or "unknown", action)]


def build_copy(case: dict[str, Any], action: str) -> tuple[str, str, dict[str, Any]]:
    """Returns (text_with_link_placeholder, copy_source, validation).

    The LLM draft is used only if it passes validation; otherwise the static template
    is used and the rejection is kept on the record.
    """
    category = case.get("current_category") or "unknown"
    validation: dict[str, Any] = {"ok": True, "problems": [], "source": "static_template"}

    if llm.copy_enabled():
        try:
            draft = llm.draft_copy(category, action, _amount_str(case), config.MERCHANT_NAME)
            validation = validate_copy(draft, case)
            validation["source"] = "llm_draft"
            validation["draft"] = draft
            if validation["ok"]:
                return render_slots(draft, case), "llm_draft", validation
            log.info("LLM copy rejected for case %s: %s", case["id"], validation["problems"])
        except Exception as exc:
            validation = {"ok": False, "problems": [f"llm error: {type(exc).__name__}: {exc}"],
                          "source": "llm_draft"}

    text = static_template(category, action, case)
    fallback_check = validate_copy(text, case)
    validation["fallback_validation"] = fallback_check
    if not fallback_check["ok"]:  # a static template must never fail its own validator
        raise AssertionError(
            f"static template for ({category}, {action}) fails copy validation: {fallback_check['problems']}"
        )
    return render_slots(text, case), "static_template", validation


def inject_link(text: str, url: str) -> str:
    return text.replace(config.LINK_PLACEHOLDER, url)


# ------------------------------------------------------------------- executions
def _simulated_link(case: dict[str, Any]) -> str:
    return config.SIMULATED_LINK_BASE + case["id"]


def retry_charge(case: dict[str, Any]) -> dict[str, Any]:
    """A silent re-charge of the existing mandate. No customer contact.

    Razorpay test mode has no clean, stable API for forcing a subscription charge
    retry (docs/04 §6.1), so this is recorded as `simulated` — honestly — rather than
    dressed up as a live call.
    """
    return {
        "mode": "simulated",
        "razorpay_ref": None,
        "status": "success",
        "simulated_channel": None,
        "message_copy": None,
        "copy_source": None,
        "copy_validation": None,
        "result_payload": {
            "action": "retry_charge",
            "subscription_id": case["subscription_id"],
            "amount_paise": case["amount_at_risk_paise"],
            "note": ("silent mandate re-charge; recorded as simulated because test mode exposes "
                     "no stable API for forcing a subscription charge retry"),
        },
    }


def _create_payment_link(case: dict[str, Any], description: str) -> tuple[Optional[str], Optional[str], dict[str, Any]]:
    """Try a real test-mode Payment Link. Returns (link_id, short_url, payload).

    Notifications are explicitly off: this project never sends anything to anyone.
    """
    global _live_link_budget
    if _live_link_budget <= 0 or not razorpay_configured():
        return None, None, {"skipped": "live link budget exhausted or Razorpay not configured"}
    try:
        # Spent before the call and never refunded on failure — deliberately
        # fail-closed. A refund-on-error would let a persistently failing endpoint be
        # retried without bound, which is exactly the rate-limit the budget exists to
        # respect. Over-counting a link costs a demo; under-counting costs the account.
        _live_link_budget -= 1
        resp = razorpay_client().payment_link.create(
            {
                "amount": int(case["amount_at_risk_paise"]),
                "currency": case["currency"],
                "description": description,
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "notes": {"case_id": case["id"], "synthetic": str(bool(case["synthetic"])).lower()},
            }
        )
        return resp.get("id"), resp.get("short_url"), resp
    except Exception as exc:
        log.warning("Payment Link creation failed for case %s: %s", case["id"], exc)
        return None, None, {"error": f"{type(exc).__name__}: {exc}"}


def send_update_link(case: dict[str, Any], action: str = "SEND_UPDATE_LINK") -> dict[str, Any]:
    """Create (optionally real) a Payment Link and log a simulated notification."""
    description = (
        f"{config.SYNTHETIC_DISCLOSURE} Update payment for subscription "
        f"{case['subscription_id']} — Rs {_amount_str(case)}"
    )
    link_id, short_url, payload = _create_payment_link(case, description)

    mode = "razorpay_test" if link_id else "simulated"
    url = short_url or _simulated_link(case)

    text, copy_source, validation = build_copy(case, action)
    message = inject_link(text, url)

    return {
        "mode": mode,
        "razorpay_ref": link_id,
        "status": "success",
        "simulated_channel": "sms",
        "message_copy": message,
        "copy_source": copy_source,
        "copy_validation": validation,
        "result_payload": {
            "action": "send_update_link" if action == "SEND_UPDATE_LINK" else "send_promise_offer",
            "link_url": url,
            "notification": "SIMULATED — composed and logged, never transmitted",
            "razorpay_response": payload,
        },
    }


def send_promise_offer(case: dict[str, Any]) -> dict[str, Any]:
    """Promise-to-pay: a link plus a short grace window before human handoff.

    Reachable only as insufficient_funds attempt 3, so there is at most one per case,
    ever — it consumes the third and final attempt.
    """
    result = send_update_link(case, action="PROMISE_TO_PAY")
    deadline = clock.plus_hours(clock.now(), config.PROMISE_WINDOW_HOURS)
    result["result_payload"]["promise_deadline"] = clock.to_iso(deadline)
    result["result_payload"]["promise_window_hours"] = config.PROMISE_WINDOW_HOURS
    return result


_HANDLERS = {
    "RETRY_LATER": lambda case: retry_charge(case),
    "SEND_UPDATE_LINK": lambda case: send_update_link(case),
    "PROMISE_TO_PAY": lambda case: send_promise_offer(case),
}


def execute_decision(decision: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Run one scheduled decision, after re-checking every invariant against freshly
    loaded state (docs/04 §6). Returns the ExecutionRecord, or None if it was stopped
    or deferred."""
    case = cases.get(decision["case_id"])
    if case is None:
        return None
    if case["status"] != "open":
        db.update("intervention_decision", decision["id"], {"status": "superseded"})
        return None

    verdict = invariants.check(case, decision["action"], phase="pre_execution")
    checks = json.loads(decision["invariant_check"])
    checks["pre_execution"] = dict(verdict.details)
    db.update("intervention_decision", decision["id"], {"invariant_check": json.dumps(checks, sort_keys=True)})

    if verdict.is_stop:
        db.update("intervention_decision", decision["id"], {"status": "blocked_by_invariant"})
        cases.transition(
            case["id"],
            verdict.status,
            summary=(f"Attempt {decision['attempt_number']} blocked at execution by {verdict.invariant}: "
                     f"{verdict.reason}"),
            detail={
                "invariant": verdict.invariant,
                "invariant_rule": invariants.INVARIANT_TEXT[verdict.invariant],
                "phase": "pre_execution",
                "decision_id": decision["id"],
                "blocked_action": decision["action"],
                "checks": verdict.details,
            },
        )
        return None

    if verdict.is_defer:
        db.update("intervention_decision", decision["id"], {"scheduled_for": clock.to_iso(verdict.until)})
        audit.audit(
            case["id"], "decide", "system",
            (f"Attempt {decision['attempt_number']} deferred by {verdict.invariant}: {verdict.reason}; "
             f"rescheduled for {clock.to_iso(verdict.until)}"),
            {
                "invariant": verdict.invariant,
                "invariant_rule": invariants.INVARIANT_TEXT[verdict.invariant],
                "phase": "pre_execution",
                "decision_id": decision["id"],
                "action": decision["action"],
                "deferred_until": clock.to_iso(verdict.until),
                "checks": verdict.details,
            },
        )
        return None

    handler = _HANDLERS.get(decision["action"])
    if handler is None:  # STOP_HANDOFF never reaches the executor; policy closes the case
        raise ValueError(f"action {decision['action']} is not executable")

    # Claim the decision before doing anything irreversible. The conditional UPDATE is
    # the claim token: exactly one caller can move it off 'scheduled', so a decision
    # picked up twice — two overlapping ticks, or a tick racing the webhook path —
    # creates one Payment Link, not two. Losing the claim means someone else owns this
    # execution, so return without touching the case.
    if db.execute(
        "UPDATE intervention_decision SET status = 'executed' WHERE id = ? AND status = 'scheduled'",
        (decision["id"],),
    ).rowcount != 1:
        log.info("decision %s already claimed by another worker", decision["id"])
        return None

    # Then claim the attempt itself. This is where I1 is actually enforced: the gate
    # above only *read* attempt_count, and another thread can execute between that read
    # and this line. reserve_attempt refuses at the cap atomically.
    if not cases.reserve_attempt(case["id"], decision["action"]):
        db.update("intervention_decision", decision["id"], {"status": "blocked_by_invariant"})
        cases.transition(
            case["id"], "stopped_max_attempts",
            summary=(f"Attempt {decision['attempt_number']} blocked at execution by I1: "
                     f"all {config.MAX_ATTEMPTS} attempts were already consumed"),
            detail={
                "invariant": "I1",
                "invariant_rule": invariants.INVARIANT_TEXT["I1"],
                "phase": "pre_execution",
                "decision_id": decision["id"],
                "blocked_action": decision["action"],
                "note": "attempt slot lost to a concurrent execution",
            },
        )
        return None

    try:
        result = handler(case)
    except Exception as exc:
        log.exception("execution failed for decision %s", decision["id"])
        result = {
            "mode": "simulated", "razorpay_ref": None, "status": "failed",
            "simulated_channel": None, "message_copy": None, "copy_source": None,
            "copy_validation": None,
            "result_payload": {"error": f"{type(exc).__name__}: {exc}"},
        }

    execution_id = db.new_id("exe")
    executed_at = clock.now_iso()
    db.insert(
        "execution_record",
        {
            "id": execution_id,
            "decision_id": decision["id"],
            "case_id": case["id"],
            "action": decision["action"],
            "mode": result["mode"],
            "razorpay_ref": result.get("razorpay_ref"),
            "simulated_channel": result.get("simulated_channel"),
            "message_copy": result.get("message_copy"),
            "copy_source": result.get("copy_source"),
            "copy_validation": json.dumps(result.get("copy_validation"), default=str, sort_keys=True)
            if result.get("copy_validation") is not None else None,
            "status": result["status"],
            "result_payload": json.dumps(result.get("result_payload"), default=str, sort_keys=True),
            "executed_at": executed_at,
            "synthetic": case["synthetic"],
        },
    )
    # The decision was already marked executed when it was claimed, and the attempt was
    # consumed by reserve_attempt() before the handler ran.
    audit.audit(
        case["id"], "execute", "system",
        (f"Attempt {decision['attempt_number']}: {decision['action']} executed via {result['mode']} "
         f"({result['status']})"),
        {
            "execution_id": execution_id,
            "decision_id": decision["id"],
            "action": decision["action"],
            "mode": result["mode"],
            "razorpay_ref": result.get("razorpay_ref"),
            "simulated_channel": result.get("simulated_channel"),
            "message_copy": result.get("message_copy"),
            "copy_source": result.get("copy_source"),
            "copy_validation": result.get("copy_validation"),
            "result_payload": result.get("result_payload"),
        },
    )
    return db.row_to_dict(db.query_one("SELECT * FROM execution_record WHERE id = ?", (execution_id,)))


# -------------------------------------------------------------------- the tick
def due_decisions(now_iso: Optional[str] = None, include_synthetic: bool = True) -> list[dict[str, Any]]:
    return db.rows_to_dicts(
        db.query(
            "SELECT d.* FROM intervention_decision d"
            " WHERE d.status = 'scheduled' AND d.scheduled_for <= ?"
            + ("" if include_synthetic else " AND d.synthetic = 0")
            # rowid, not id: ULIDs carry a random tail, so ties on scheduled_for would
            # order arbitrarily and make a seeded batch non-reproducible.
            + " ORDER BY d.scheduled_for ASC, d.decided_at ASC, d.rowid ASC",
            (now_iso or clock.now_iso(),),
        )
    )


def _close_lapsed_promises(include_synthetic: bool = True) -> int:
    """A promise-to-pay that was not honoured inside its window goes to the human
    queue — it is never retried, because attempt 3 was the last one."""
    closed = 0
    rows = db.query(
        "SELECT e.case_id AS case_id, e.executed_at AS executed_at, e.id AS execution_id"
        " FROM execution_record e JOIN recovery_case c ON c.id = e.case_id"
        " WHERE e.action = 'PROMISE_TO_PAY' AND c.status = 'open'"
        + ("" if include_synthetic else " AND c.synthetic = 0")
    )
    for row in rows:
        deadline = clock.plus_hours(clock.parse_iso(row["executed_at"]), config.PROMISE_WINDOW_HOURS)
        if clock.now() >= deadline:
            cases.transition(
                row["case_id"], "stopped_handoff",
                summary=(f"Promise-to-pay window of {config.PROMISE_WINDOW_HOURS}h lapsed unpaid; "
                         "handed off to the human queue with a complete case file"),
                detail={"execution_id": row["execution_id"], "promise_deadline": clock.to_iso(deadline)},
            )
            closed += 1
    return closed


def _close_expired_episodes(include_synthetic: bool = True) -> int:
    """No case is left a zombie: an open case that outlives its episode window closes
    honestly rather than waiting for a webhook that may never arrive."""
    closed = 0
    sql = "SELECT * FROM recovery_case WHERE status = 'open'" + (
        "" if include_synthetic else " AND synthetic = 0")
    for row in db.query(sql):
        case = db.row_to_dict(row)
        if invariants.episode_expired(case):
            # A control-arm case reaching the end of its window was never intervened
            # on, so it closes under its own reason rather than being mixed in with
            # cases the agent tried and could not recover.
            holdout = int(case.get("is_holdout") or 0) == 1
            cases.transition(
                case["id"],
                "stopped_holdout" if holdout else "stopped_cooldown_expired",
                summary=(
                    f"Control arm: observed for the full {config.EPISODE_WINDOW_DAYS}-day window "
                    "with no intervention, and did not recover on its own"
                    if holdout else
                    f"Episode window of {config.EPISODE_WINDOW_DAYS} days closed without recovery; "
                    "handed off to the human queue"
                ),
                detail={"created_at": case["created_at"],
                        "arm": "control" if holdout else "treated",
                        "episode_deadline": clock.to_iso(invariants.episode_deadline(case))},
            )
            closed += 1
    return closed


def tick(include_synthetic: bool = True) -> dict[str, Any]:
    """Advance every case whose next step is due. Called every 30s in live mode and
    directly by the batch runner against the simulated clock (docs/01 §2).

    `include_synthetic=False` is what the background loop in main.py uses, and it exists
    because the two clocks share one database. A batch replay writes its cases against a
    *simulated* clock; to the live loop, running on the wall clock, every one of those
    cases looks weeks old and therefore expired — so an unfiltered background tick would
    quietly close a finished experiment and report it as a batch that recovered nothing.
    Synthetic rows advance only under the clock that created them: the batch runner, or an
    operator pressing Tick, both of which call this with the default.
    """
    executions: list[dict[str, Any]] = []
    for decision in due_decisions(include_synthetic=include_synthetic):
        record = execute_decision(decision)
        if record is not None:
            executions.append(record)
    promises = _close_lapsed_promises(include_synthetic)
    expired = _close_expired_episodes(include_synthetic)
    return {
        "at": clock.now_iso(),
        "executions": executions,
        "promises_lapsed": promises,
        "episodes_expired": expired,
    }
