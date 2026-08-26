"""Rule table R1-R7 (docs/04 §3.2)."""
from __future__ import annotations

import pytest

from app import config, diagnosis

CASES = [
    ("card_expired", "The card used for this payment has expired", "R1", "card_expired"),
    ("payment_failed", "Payment failed: expired card. Please use another card", "R1", "card_expired"),
    ("payment_failed", "Your card has insufficient funds", "R2", "insufficient_funds"),
    ("payment_failed", "Transaction amount exceeds limit set on the card", "R2", "insufficient_funds"),
    ("payment_failed", "3DSecure authentication failed", "R3", "authentication_failed"),
    ("payment_failed", "OTP entry was not completed in time", "R3", "authentication_failed"),
    ("payment_failed", "Card blocked by the issuing bank", "R4", "invalid_payment_method"),
    ("payment_failed", "The mandate cancelled by the customer cannot be charged", "R4",
     "invalid_payment_method"),
    ("payment_failed", "Transaction declined by the issuing bank (do_not_honour)", "R5",
     "issuer_declined"),
    ("payment_failed", "The transaction was declined by your bank", "R5", "issuer_declined"),
    ("payment_failed", "Payment timed out at the bank's end", "R6", "issuer_declined"),
]


@pytest.mark.parametrize("reason,description,rule,category", CASES)
def test_rule_matches(reason, description, rule, category):
    verdict = diagnosis.apply_rules(
        {"error_reason": reason, "error_description": description, "error_code": "BAD_REQUEST_ERROR"})
    assert verdict is not None, "expected a rule to match"
    assert verdict["category"] == category
    assert verdict["matched_rule"] == rule
    assert verdict["method"] == "rule"
    assert verdict["confidence"] == 1.0


def test_r7_empty_fields_short_circuits_to_unknown_without_a_model():
    verdict = diagnosis.apply_rules({"error_reason": None, "error_description": None, "error_code": None})
    assert verdict["category"] == "unknown"
    assert verdict["matched_rule"] == config.RULE_R7


def test_expired_otp_is_authentication_not_card_expired():
    """R1 must not swallow 'expired' when it belongs to the OTP, not the card."""
    verdict = diagnosis.apply_rules(
        {"error_reason": "payment_failed", "error_description": "The OTP has expired", "error_code": ""})
    assert verdict["category"] == "authentication_failed"


def test_unmatched_text_falls_through_to_the_model():
    verdict = diagnosis.apply_rules({
        "error_reason": "payment_failed",
        "error_description": "Bank returned a negative response for the standing instruction",
        "error_code": "BAD_REQUEST_ERROR"})
    assert verdict is None, "this string must reach the LLM fallback, not a rule"


def test_every_rule_maps_into_the_fixed_enum():
    for _rule, _patterns, category in config.RULES:
        assert category in config.CATEGORIES


def test_rule_order_decides_an_ambiguous_string():
    """When one error string matches two rules, table order decides — not specificity,
    not the longer match. This pins that contract so reordering config.RULES cannot
    silently reclassify traffic: R1 precedes R2, so card_expired wins here."""
    verdict = diagnosis.apply_rules({
        "error_reason": "payment_failed",
        "error_description": "The card has expired and the account has insufficient balance",
        "error_code": ""})
    assert verdict["matched_rule"] == "R1"
    assert verdict["category"] == "card_expired"

    order = [rule for rule, _patterns, _category in config.RULES]
    assert order.index("R1") < order.index("R2"), "precedence above depends on this order"
