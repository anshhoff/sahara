#!/usr/bin/env python3
"""Smoke-test the configured LLM provider before you trust a batch run.

Worth running because of how the boundary is designed: when the provider is
unreachable, every call collapses to `unknown`, every ambiguous case stops, and the
batch still exits 0 with green acceptance checks. That is correct behaviour — but it
looks identical to "I forgot to set my API key". This script tells the two apart.

    python scripts/check_llm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, executor, llm  # noqa: E402

# Strings that deliberately miss every rule R1-R6, so the model is the only thing that
# can classify them. These mirror the LLM-fallback flavours in the synthetic generator.
PROBES = [
    ("The customer's bank did not permit this standing instruction at this time",
     "issuer_declined"),
    ("Recurring debit rejected: cardholder record is no longer active with the issuer",
     "invalid_payment_method"),
    ("Bank returned a negative response for the standing instruction", "issuer_declined"),
    ("lorem ipsum dolor sit amet consectetur adipiscing elit", "unknown"),
]

DEMO_CASE = {
    "id": "case_smoketest",
    "amount_at_risk_paise": 49900,
    "current_category": "card_expired",
    "currency": "INR",
    "synthetic": 1,
}


def main() -> int:
    print(f"provider   {config.LLM_PROVIDER}")
    print(f"base_url   {config.LLM_BASE_URL}")
    print(f"model      {config.LLM_MODEL}")
    print(f"api_key    {'set (' + str(len(config.LLM_API_KEY)) + ' chars)' if config.LLM_API_KEY else 'EMPTY'}")
    print(f"copy       {'enabled' if llm.copy_enabled() else 'disabled'}")

    problem = llm.misconfiguration()
    if problem:
        print(f"\nMISCONFIGURED: {problem}")
        return 2

    if not llm.enabled():
        print("\nLLM_PROVIDER=none — no model is called at all. Ambiguous cases become "
              "`unknown` and stop, copy uses static templates. This is a supported mode, "
              "not an error.")
        return 0

    print("\n--- call #1: classification (the only classification call in the system) ---")
    reached, correct = 0, 0
    for text, expected in PROBES:
        out = llm.classify({
            "error_code": "BAD_REQUEST_ERROR",
            "error_reason": "payment_failed",
            "error_description": text,
            "error_step": "payment_authorization",
        })
        failed = out["category"] == "unknown" and out["confidence"] == 0.0 and out["llm_raw_response"]
        if not failed:
            reached += 1
        if out["category"] == expected:
            correct += 1
        mark = "ok " if out["category"] == expected else "..."
        print(f"  [{mark}] {out['category']:<22} conf {out['confidence']:.2f}  "
              f"expected {expected:<22} | {text[:52]}")
        if failed:
            print(f"        provider error: {str(out['llm_raw_response'])[:160]}")

    print("\n--- call #2: copy drafting (validated deterministically before use) ---")
    if llm.copy_enabled():
        text, source, validation = executor.build_copy(DEMO_CASE, "SEND_UPDATE_LINK")
        print(f"  source     {source}")
        print(f"  text       {text}")
        if source == "static_template" and validation.get("problems"):
            print(f"  rejected   {validation['problems']}")
            print("  (the draft failed validation and the static template was used — "
                  "this is the fallback working, not a crash)")
    else:
        print("  copy drafting disabled (LLM_COPY_ENABLED=false); static templates only")

    print(f"\nreached the provider on {reached}/{len(PROBES)} classification calls; "
          f"{correct}/{len(PROBES)} matched the expected category")
    if reached == 0:
        print("\nFAILED: no call reached the provider. Every ambiguous case would stop as "
              "`unknown`. Check LLM_BASE_URL, LLM_MODEL and LLM_API_KEY against your "
              "provider's own docs (docs/10 §5.3) — model ids in particular change often.")
        return 1
    if reached < len(PROBES):
        print("\nPARTIAL: some calls failed. Rate limiting is the usual cause on a free tier; "
              "a 429 is handled exactly like any other error (-> unknown -> stop).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
