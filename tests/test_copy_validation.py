"""The deterministic gate on drafted copy (docs/04 §6.3)."""
from __future__ import annotations

from app import cases, config, executor


def _case(fresh_db, amount=49900, category="card_expired"):
    c = cases.create(subscription_id="SYNTH-sub-c", customer_id="SYNTH-cust-c",
                     amount_at_risk_paise=amount, synthetic=True)
    cases.set_category(c["id"], category)
    return cases.get(c["id"])


GOOD = "[SYNTHETIC DEMO] Your subscription payment of Rs 499 could not be collected. Pay here: {LINK}"


def test_a_well_formed_draft_passes(fresh_db):
    assert executor.validate_copy(GOOD, _case(fresh_db))["ok"]


def test_missing_synthetic_disclosure_is_rejected(fresh_db):
    v = executor.validate_copy(GOOD.replace(config.SYNTHETIC_DISCLOSURE, ""), _case(fresh_db))
    assert not v["ok"] and any("disclosure" in p for p in v["problems"])


def test_an_invented_amount_is_rejected(fresh_db):
    v = executor.validate_copy(GOOD.replace("Rs 499", "Rs 4990"), _case(fresh_db))
    assert not v["ok"] and any("numbers other than" in p for p in v["problems"])


def test_any_extra_number_is_rejected(fresh_db):
    v = executor.validate_copy(GOOD + " Valid for 7 days.", _case(fresh_db))
    assert not v["ok"]


def test_a_model_written_url_is_rejected(fresh_db):
    v = executor.validate_copy(GOOD.replace("{LINK}", "https://rzp.io/i/abc"), _case(fresh_db))
    assert not v["ok"]
    assert any("URL" in p for p in v["problems"]) or any("{LINK}" in p for p in v["problems"])


def test_a_duplicated_placeholder_is_rejected(fresh_db):
    v = executor.validate_copy(GOOD + " {LINK}", _case(fresh_db))
    assert not v["ok"]


def test_forbidden_words_are_rejected(fresh_db):
    for word in config.COPY_FORBIDDEN:
        v = executor.validate_copy(GOOD.replace("Pay here", f"{word.title()} — pay here"), _case(fresh_db))
        assert not v["ok"], word


def test_overlong_copy_is_rejected(fresh_db):
    padded = GOOD.replace("Pay here", "Pay here" + " and please act soon" * 20)
    assert not executor.validate_copy(padded, _case(fresh_db))["ok"]


def test_every_static_template_passes_its_own_validator(fresh_db):
    """The system must be fully functional with the copy model switched off."""
    for (category, action) in config.STATIC_TEMPLATES:
        case = _case(fresh_db, category=category)
        text = executor.static_template(category, action, case)
        v = executor.validate_copy(text, case)
        assert v["ok"], (category, action, v["problems"])


def test_a_template_exists_for_every_category_and_contact_action():
    for category in config.CATEGORIES:
        for action in sorted(config.CONTACT_ACTIONS):
            assert (category, action) in config.STATIC_TEMPLATES


def test_a_rejected_draft_falls_back_to_the_template_and_keeps_the_rejection(fresh_db, monkeypatch):
    from app import llm

    monkeypatch.setattr(llm, "copy_enabled", lambda: True)
    monkeypatch.setattr(llm, "draft_copy",
                        lambda *a, **k: "Pay Rs 99999 now or face a penalty. https://evil.example")
    case = _case(fresh_db)
    text, source, validation = executor.build_copy(case, "SEND_UPDATE_LINK")
    assert source == "static_template"
    assert validation["ok"] is False and validation["problems"]
    assert config.SYNTHETIC_DISCLOSURE in text and config.LINK_PLACEHOLDER in text


def test_link_injection_happens_after_validation(fresh_db):
    case = _case(fresh_db)
    text, _, _ = executor.build_copy(case, "SEND_UPDATE_LINK")
    injected = executor.inject_link(text, "https://example.invalid/pay/x")
    assert config.LINK_PLACEHOLDER not in injected
    assert "https://example.invalid/pay/x" in injected
