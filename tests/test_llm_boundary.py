"""The LLM boundary, checked mechanically rather than asserted in prose
(docs/00 §3, docs/04 §8)."""
from __future__ import annotations

import re
from pathlib import Path

from app import cases, config, db, diagnosis, llm, webhooks
from tests.conftest import failure_payload

APP = Path(__file__).resolve().parent.parent / "app"


def test_only_llm_py_imports_the_model_client():
    """grep -rl 'import openai' app/ -> app/llm.py, and nothing else."""
    pattern = re.compile(r"^\s*(?:from|import)\s+openai\b", re.M)
    offenders = [p.name for p in APP.glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == ["llm.py"], offenders


def test_the_model_module_never_touches_razorpay_or_the_database():
    src = (APP / "llm.py").read_text(encoding="utf-8")
    assert "import razorpay" not in src
    assert re.search(r"^\s*(?:from|import)\s+razorpay\b", src, re.M) is None
    assert "from app import db" not in src and "app.db" not in src


def test_invariants_module_is_independent_of_policy_and_the_model():
    src = (APP / "invariants.py").read_text(encoding="utf-8")
    for forbidden in ("policy", "llm", "executor"):
        assert re.search(rf"^\s*from app import .*\b{forbidden}\b", src, re.M) is None, forbidden


def test_provider_none_produces_unknown_without_any_client():
    assert config.LLM_PROVIDER == "none"
    out = llm.classify({"error_code": "X", "error_reason": "y", "error_description": "z",
                        "error_step": "s"})
    assert out["category"] == "unknown"
    assert out["llm_model"] is None


def test_low_confidence_collapses_to_unknown(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient(
        '{"category": "card_expired", "confidence": 0.4, "rationale": "guessing"}'))
    out = llm.classify({"error_code": "X"})
    assert out["category"] == "unknown"
    assert out["confidence"] == 0.4


def test_an_invented_category_is_rejected_by_the_schema(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient(
        '{"category": "customer_is_annoyed", "confidence": 0.99, "rationale": "made up"}'))
    assert llm.classify({"error_code": "X"})["category"] == "unknown"


def test_prose_instead_of_json_collapses_to_unknown(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient("I think the card probably expired."))
    assert llm.classify({"error_code": "X"})["category"] == "unknown"


def test_json_wrapped_in_a_code_fence_is_still_extracted(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient(
        'Sure!\n```json\n{"category": "issuer_declined", "confidence": 0.95, "rationale": "bank"}\n```'))
    out = llm.classify({"error_code": "X"})
    assert out["category"] == "issuer_declined" and out["method"] == "llm"


def test_a_provider_exception_collapses_to_unknown(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)

    class Boom:
        @property
        def chat(self):
            raise TimeoutError("provider is down")

    monkeypatch.setattr(llm, "_client", lambda: Boom())
    out = llm.classify({"error_code": "X"})
    assert out["category"] == "unknown" and out["confidence"] == 0.0
    assert "TimeoutError" in out["llm_raw_response"]


def test_a_hallucinated_but_valid_category_is_still_capped_by_the_policy(fresh_db, monkeypatch):
    """Worst realistic case: the model is confidently wrong. The case still gets at
    most three bounded attempts, and every one of them is audited."""
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "classify", lambda fields: {
        "category": "insufficient_funds", "method": "llm", "matched_rule": None,
        "confidence": 0.99, "llm_model": "fake", "llm_raw_response": "{}"})
    webhooks.intake(failure_payload(
        error=("X", "totally_unmatchable_reason", "nothing a rule can match here", "bank")),
        source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    assert case["current_category"] == "insufficient_funds"
    assert case["attempt_count"] <= config.MAX_ATTEMPTS


def test_llm_diagnoses_are_labelled_as_such_in_the_audit_trail(fresh_db, monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "classify", lambda fields: {
        "category": "issuer_declined", "method": "llm", "matched_rule": None,
        "confidence": 0.91, "llm_model": "fake-model", "llm_raw_response": '{"category":"issuer_declined"}'})
    webhooks.intake(failure_payload(
        error=("X", "totally_unmatchable_reason", "nothing a rule can match here", "bank")),
        source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    actors = [r["actor"] for r in db.query(
        "SELECT actor FROM audit_log WHERE case_id = ? AND stage = 'diagnose'", (case["id"],))]
    assert actors == ["llm"]
    row = db.query_one("SELECT * FROM diagnosis_result WHERE case_id = ?", (case["id"],))
    assert row["llm_model"] == "fake-model" and row["llm_raw_response"]


class _FakeClient:
    """Minimal stand-in shaped like the chat-completions client."""

    def __init__(self, content: str):
        self._content = content

    @property
    def chat(self):
        outer = self

        class _Completions:
            def create(self, **kwargs):
                class _Msg:
                    content = outer._content

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        return _Chat()
