"""Diagnose — rules first, model only for what the rules cannot reach (docs/04 §3).

The rule table resolves the overwhelming majority of real failures. The LLM is only
consulted when R1–R6 all miss AND there is actually text to classify; when the error
fields are empty there is nothing to classify, so R7 short-circuits to `unknown`
without troubling a model.
"""
from __future__ import annotations

from typing import Any, Optional

from app import audit, cases, clock, config, db, fencing, llm


def match_input(event: dict[str, Any]) -> str:
    parts = [
        event.get("error_reason") or "",
        event.get("error_description") or "",
        event.get("error_code") or "",
    ]
    return " ".join(p for p in parts if p).strip().lower()


def apply_rules(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return a rule verdict, or None when no rule matched and the LLM should try."""
    text = match_input(event)
    if not text:
        # R7 — nothing to classify. Never guessed, never sent to a model.
        return {
            "category": "unknown",
            "method": "rule",
            "matched_rule": config.RULE_R7,
            "confidence": 1.0,
            "llm_model": None,
            "llm_raw_response": None,
        }
    # First hit wins, and `text` concatenates reason + description + code — so an error
    # string carrying two rule keywords ("card expired" inside an issuer decline message,
    # say) resolves by the order of config.RULES, not by specificity. That is
    # deterministic and reproducible, but it is table order doing the deciding: adding a
    # rule in the wrong position silently reclassifies traffic. Order is part of the
    # rule table's contract, and test_rule_order_decides_an_ambiguous_string pins it.
    for rule_id, patterns, category in config.RULES:
        for pattern in patterns:
            if pattern in text:
                return {
                    "category": category,
                    "method": "rule",
                    "matched_rule": rule_id,
                    "confidence": 1.0,
                    "llm_model": None,
                    "llm_raw_response": None,
                    "matched_pattern": pattern,
                }
    return None


def diagnose(case: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Classify one failure event, persist a DiagnosisResult, audit it, and
    denormalise the category onto the case for the policy lookup."""
    verdict = apply_rules(event)
    used_llm = verdict is None
    stale_inference: Optional[dict[str, Any]] = None
    if used_llm:
        # Fence 3: the stale-inference guard. A model call is the one step here with
        # unbounded latency — a slow provider, a retry, a rate limit — and the case can
        # move underneath it. The fingerprint covers DECISION-RELEVANT fields only, so
        # the customer's notes changing, or `updated_at` ticking, does not trip it. A
        # guard that fires on irrelevant churn gets switched off within a week and
        # protects nothing; the value of this one is that it is quiet.
        snapshot = fencing.decision_fingerprint(cases.get(case["id"]) or case)
        verdict = llm.classify(
            {
                "error_code": event.get("error_code"),
                "error_reason": event.get("error_reason"),
                "error_description": event.get("error_description"),
                "error_step": event.get("error_step"),
            }
        )
        fresh = fencing.inference_unchanged(cases.get(case["id"]) or case, snapshot, action="CLASSIFY")
        if fresh.blocks:
            # The classification was computed against a world that no longer exists.
            # It is recorded — the model did produce it, and hiding that would make the
            # trail claim less than it knows — but it is not acted on. `unknown` always
            # stops (I4), which is the correct destination for an answer to a question
            # that is no longer the question being asked.
            stale_inference = {"snapshot": snapshot, "reason": fresh.reason,
                               "superseded_category": verdict.get("category")}
            verdict = dict(verdict, category="unknown", confidence=0.0)

    diagnosis_id = db.new_id("dia")
    db.insert(
        "diagnosis_result",
        {
            "id": diagnosis_id,
            "case_id": case["id"],
            "failure_event_id": event["id"],
            "category": verdict["category"],
            "method": verdict["method"],
            "matched_rule": verdict.get("matched_rule"),
            "confidence": float(verdict.get("confidence") or 0.0),
            "llm_model": verdict.get("llm_model"),
            "llm_raw_response": verdict.get("llm_raw_response"),
            "created_at": clock.now_iso(),
            "synthetic": case["synthetic"],
        },
    )
    cases.set_category(case["id"], verdict["category"])

    if verdict["method"] == "rule":
        actor = "system"
        summary = (
            f"Diagnosed {verdict['category']} by rule {verdict['matched_rule']}"
            + (f" (matched '{verdict['matched_pattern']}')" if verdict.get("matched_pattern") else "")
        )
    else:
        actor = "llm"  # every LLM contribution is visibly labelled in the trail
        summary = (
            f"Diagnosed {verdict['category']} by model {verdict.get('llm_model')} "
            f"at confidence {verdict.get('confidence'):.2f}"
            + (" — decision-relevant state changed while the inference was in flight, so the "
               "model's answer was recorded and not acted on" if stale_inference else
               "" if verdict["category"] != "unknown" else
               " — below threshold or unparseable, forced to unknown")
        )

    audit.audit(
        case["id"],
        "diagnose",
        actor,
        summary,
        {
            "diagnosis_id": diagnosis_id,
            "failure_event_id": event["id"],
            "category": verdict["category"],
            "method": verdict["method"],
            "matched_rule": verdict.get("matched_rule"),
            "confidence": verdict.get("confidence"),
            "llm_model": verdict.get("llm_model"),
            "llm_raw_response": verdict.get("llm_raw_response"),
            "rationale": verdict.get("rationale"),
            "classified_text": match_input(event) or None,
            "confidence_threshold": config.LLM_CONFIDENCE_THRESHOLD,
            "stale_inference": stale_inference,
        },
    )
    return db.row_to_dict(db.query_one("SELECT * FROM diagnosis_result WHERE id = ?", (diagnosis_id,)))
