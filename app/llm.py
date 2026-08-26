"""The ONLY module in app/ that talks to a language model.

Two leaf calls, both of which return data and never trigger an action:

  1. classify()   — ambiguous failure reasons -> one of six fixed categories.
  2. draft_copy() — notification copy, which executor.py then validates deterministically.

The model has no tools, no credentials and no callbacks. Every failure mode of the
provider — refusal, timeout, rate limit, prose instead of JSON, an invented category,
low self-reported confidence, a dead server, or no provider configured at all —
collapses to `unknown`, and `unknown` always stops (invariant I4). The guardrail is
this file plus invariants.py, not model quality (docs/04 §3.3, §8).

The import of the provider client is deliberately lazy and confined to `_client()`
so that LLM_PROVIDER=none needs no LLM package installed at all.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You classify failed recurring card payments for an Indian payment gateway.

Choose EXACTLY ONE category from this fixed list:
- card_expired: the stored card is past its expiry date.
- insufficient_funds: the account balance or credit limit was not enough.
- issuer_declined: the bank refused the charge for an opaque or generic reason,
  including "do not honour", generic declines, and transient gateway/bank errors.
- authentication_failed: 3-D Secure, OTP or mandate authentication failed or expired.
- invalid_payment_method: the instrument itself is unusable — blocked, lost, stolen,
  closed account, cancelled mandate, invalid card or VPA.
- unknown: the evidence does not clearly support any category above.

Rules:
- If you are not confident, answer "unknown". Guessing is worse than admitting ignorance.
- Reply with ONE JSON object and nothing else:
  {"category": "<one of the six>", "confidence": <number between 0 and 1>, "rationale": "<one sentence>"}
"""

USER_PROMPT = """Classify this failed payment.

error_code: {error_code}
error_reason: {error_reason}
error_description: {error_description}
error_step: {error_step}

JSON only."""

COPY_PROMPT = """Write one SMS to a customer whose subscription payment failed.

Merchant: {merchant}
Failure category: {category}
Amount due: Rs {amount}
Intent: {intent}

Hard requirements:
- Begin with the literal text {disclosure}
- Include the literal placeholder {link_placeholder} exactly once, at the end. Never write a URL.
- The ONLY number anywhere in the message must be {amount}. No dates, no counts, no hours.
- Under 300 characters. Plain, calm, factual. No emoji.
- Never use the words: refund, guarantee, legal, penalty, last chance.
- Do not promise anything about the account beyond paying the amount due.

Reply with the message text only."""

INTENTS = {
    "SEND_UPDATE_LINK": "ask them to update their payment method or pay the amount directly",
    "PROMISE_TO_PAY": "offer them a short window to pay before the subscription is handed to support",
}


def enabled() -> bool:
    return config.LLM_PROVIDER != config.PROVIDER_NONE


def copy_enabled() -> bool:
    return enabled() and config.LLM_COPY_ENABLED


# --------------------------------------------------------------------- client
_client_cache: Any = None


def _client():
    """Build the OpenAI-compatible client. One library reaches Ollama (local),
    Groq, OpenRouter and Google AI Studio — only base_url and model differ."""
    global _client_cache
    if _client_cache is None:
        from openai import OpenAI  # imported here, and nowhere else in app/

        _client_cache = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    return _client_cache


def misconfiguration() -> Optional[str]:
    """Catch the one mistake that fails silently: selecting a hosted provider while
    LLM_BASE_URL still points at the local Ollama default. Every call would then error,
    every ambiguous case would collapse to `unknown` and stop, and the batch would look
    fine while quietly having no model at all."""
    if config.LLM_PROVIDER != config.PROVIDER_HOSTED:
        return None
    if "localhost" in config.LLM_BASE_URL or "127.0.0.1" in config.LLM_BASE_URL:
        return (f"LLM_PROVIDER={config.PROVIDER_HOSTED} but LLM_BASE_URL is {config.LLM_BASE_URL!r} "
                "(the local Ollama default). Set LLM_BASE_URL to your provider's endpoint.")
    if not config.LLM_API_KEY or config.LLM_API_KEY == "ollama":
        return (f"LLM_PROVIDER={config.PROVIDER_HOSTED} but LLM_API_KEY is still the local "
                "placeholder. Set it to the key from your provider.")
    return None


def _chat(messages: list[dict[str, str]], *, json_mode: bool, temperature: float) -> str:
    """One call, one place. Hosted free tiers vary in whether a given model accepts
    `response_format`; if a provider rejects it we retry once without it. That is safe
    because the enum is enforced by our own validator, never by the provider."""
    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "timeout": config.LLM_TIMEOUT_S,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(**kwargs)
    except Exception as exc:
        if json_mode and _rejects_json_mode(exc):
            log.info("provider rejected response_format, retrying without it: %s", exc)
            kwargs.pop("response_format")
            resp = _client().chat.completions.create(**kwargs)
        else:
            raise
    return resp.choices[0].message.content


def _rejects_json_mode(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "response_format" in text or "json_object" in text or "json mode" in text


def reset_client() -> None:
    global _client_cache
    _client_cache = None


# ------------------------------------------------------------------ validation
try:  # the documented path: the enum is enforced client-side by pydantic
    from pydantic import BaseModel, Field, ValidationError
    from typing import Literal

    class FailureClassification(BaseModel):
        category: Literal[
            "card_expired",
            "insufficient_funds",
            "issuer_declined",
            "authentication_failed",
            "invalid_payment_method",
            "unknown",
        ]
        confidence: float = Field(ge=0.0, le=1.0)
        rationale: str = ""

    def _validate(obj: dict[str, Any]) -> tuple[str, float, str]:
        m = FailureClassification.model_validate(obj)
        return m.category, float(m.confidence), m.rationale

    _VALIDATION_ERRORS: tuple[type[BaseException], ...] = (ValidationError, ValueError, TypeError)

except Exception:  # pragma: no cover - pydantic absent (LLM_PROVIDER=none installs)
    def _validate(obj: dict[str, Any]) -> tuple[str, float, str]:
        cat = obj.get("category")
        conf = obj.get("confidence")
        if cat not in config.CATEGORIES:
            raise ValueError(f"category {cat!r} outside enum")
        conf = float(conf)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence out of range")
        return cat, conf, str(obj.get("rationale", ""))

    _VALIDATION_ERRORS = (ValueError, TypeError, KeyError)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first balanced {...} object out of a response.

    Small local models sometimes wrap JSON in prose or a code fence. If this fails,
    validation fails, and the case becomes `unknown` — which is the correct outcome.
    """
    if not text:
        raise ValueError("empty response")
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
        start = text.find("{", start + 1)
    raise ValueError("no balanced JSON object in response")


def _truncate(s: Any, n: int = 2000) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + "…[truncated]"


# ------------------------------------------------------------- call #1: classify
def classify(error_fields: dict[str, Any]) -> dict[str, Any]:
    """Classify an ambiguous failure. Returns a dict shaped for DiagnosisResult.

    The prompt carries the four Razorpay error fields and nothing else — no customer
    name, phone, email or amount ever reaches the model (docs/09 §4).
    """
    if not enabled():
        return {
            "category": "unknown",
            "method": "rule",
            "matched_rule": "R7-no-llm",
            "confidence": 1.0,
            "llm_model": None,
            "llm_raw_response": None,
        }

    raw: Optional[str] = None
    try:
        raw = _chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT.format(
                        error_code=error_fields.get("error_code") or "(none)",
                        error_reason=error_fields.get("error_reason") or "(none)",
                        error_description=error_fields.get("error_description") or "(none)",
                        error_step=error_fields.get("error_step") or "(none)",
                    ),
                },
            ],
            json_mode=True,
            temperature=0,
        )
        category, confidence, rationale = _validate(extract_json(raw))
    except Exception as exc:  # provider error, timeout, rate limit, bad JSON, bad enum
        log.warning("LLM classification failed, collapsing to unknown: %s", exc)
        return {
            "category": "unknown",
            "method": "llm",
            "matched_rule": None,
            "confidence": 0.0,
            "llm_model": config.LLM_MODEL,
            "llm_raw_response": _truncate(f"{type(exc).__name__}: {exc} | raw={raw}"),
            "rationale": "",
        }

    # Belt and braces after schema validation.
    if category not in config.CATEGORIES or not 0.0 <= confidence <= 1.0:
        category, confidence = "unknown", 0.0
    if confidence < config.LLM_CONFIDENCE_THRESHOLD:
        category = "unknown"

    return {
        "category": category,
        "method": "llm",
        "matched_rule": None,
        "confidence": confidence,
        "llm_model": config.LLM_MODEL,
        "llm_raw_response": _truncate(raw),
        "rationale": rationale,
    }


# ----------------------------------------------------------- call #2: draft copy
def draft_copy(category: str, action: str, amount_rupees: str, merchant_name: str) -> str:
    """Draft notification copy. Raises on any provider problem; the executor's
    caller catches and falls back to the static template."""
    if not copy_enabled():
        raise RuntimeError("copy drafting disabled")
    text = _chat(
        [
            {"role": "system", "content": "You write short, plain, factual service SMS copy."},
            {
                "role": "user",
                "content": COPY_PROMPT.format(
                    merchant=merchant_name,
                    category=category,
                    amount=amount_rupees,
                    intent=INTENTS.get(action, "ask them to settle the amount due"),
                    disclosure=config.SYNTHETIC_DISCLOSURE,
                    link_placeholder=config.LINK_PLACEHOLDER,
                ),
            },
        ],
        json_mode=False,
        temperature=0.3,
    )
    return (text or "").strip()
