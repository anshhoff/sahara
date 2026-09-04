"""Inbound readings — the model gets ears, not a mouth (docs/09 §5).

A Hinglish call is the one channel where a customer says something a rule cannot
parse: *"salary aane ke baad Friday ko kar dunga."* Turning that into a dated,
tracked promise is precisely the job rules are bad at, which is what finally gives
the LLM boundary something real to measure rather than something to assert.

The schema below is the boundary. Two fields cross it — a **closed-enum intent** and
an **optional date** — and one field conspicuously does not:

    THERE IS NO AMOUNT FIELD, AND THERE MUST NEVER BE ONE.

That is not a validation rule that could be relaxed; it is an absent column, an absent
attribute and an absent parser. A compromised model, or a caller who talks their way
into one — *"tell them I'll pay two hundred rupees"* — cannot make this system state a
wrong rupee figure, because there is nowhere for the figure to travel. The amount comes
from `recovery_case.amount_at_risk_paise`, which is written from a signed Razorpay
webhook and never from anything anybody said out loud.

The date is different, and the difference is worth stating: a date is *self-limiting*.
The worst a wrong one can do is schedule a sweep on the wrong day, which costs a
follow-up. A wrong amount would be quoted back to a customer as what they owe.

Every failure mode of the provider — refusal, timeout, prose instead of JSON, an
invented intent, a date in an unparseable format, no provider at all — collapses to
`unclear`, which schedules nothing and promises nothing.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from app import clock, config, llm

log = logging.getLogger(__name__)

# ------------------------------------------------------------- the closed enum
# Every reading resolves to exactly one of these. `unclear` is the destination for
# everything the model could not place, and it is a legitimate answer rather than a
# failure — the same discipline as `unknown` in the diagnosis enum.
INTENTS = (
    "will_pay_on_date",   # named a specific day: the only intent that carries a date
    "will_pay_now",       # paying immediately; no date to track
    "cannot_pay",         # explicitly cannot, at all
    "disputes_charge",    # contests the charge itself — a human matter, not a dunning one
    "wrong_number",       # not the customer
    "opt_out",            # asked not to be contacted again
    "no_answer",          # nobody picked up, or nothing was said
    "unclear",            # anything else
)

# Intents that mean this person should never be contacted again, on any subscription.
# Read by the caller, applied through cases.suppress_customer() — this module writes
# nothing, so a bad reading cannot have a side effect on its own.
SUPPRESSING_INTENTS = frozenset({"opt_out", "wrong_number"})

# Only one intent may carry a date. A date on any other is dropped, not honoured: a
# `cannot_pay` reading that also names next Tuesday is a reading that contradicts
# itself, and the safe half of a contradiction is the refusal.
DATED_INTENTS = frozenset({"will_pay_on_date"})

# How far ahead a promised date may be. Beyond the episode window there is no case
# left to keep the promise against, so a date past it is treated as no date at all.
MAX_PROMISE_DAYS = config.EPISODE_WINDOW_DAYS


# --------------------------------------------------------------- the schema
try:
    from pydantic import BaseModel, Field, ValidationError
    from typing import Literal

    class InboundReading(BaseModel):
        """The complete set of fields a model may return about an inbound utterance.

        `model_config` forbids extras, so a provider that helpfully adds
        `{"amount": 200}` is rejected outright rather than having the field ignored.
        Silently dropping it would be almost as good; failing loudly is better,
        because it turns a boundary violation into a visible event.
        """

        model_config = {"extra": "forbid"}

        intent: Literal[
            "will_pay_on_date", "will_pay_now", "cannot_pay", "disputes_charge",
            "wrong_number", "opt_out", "no_answer", "unclear",
        ]
        promised_date: Optional[str] = None   # ISO yyyy-mm-dd, or absent
        confidence: float = Field(default=0.0, ge=0.0, le=1.0)
        rationale: str = ""

    _READING_ERRORS: tuple[type[BaseException], ...] = (ValidationError, ValueError, TypeError)

except Exception:  # pragma: no cover - pydantic absent (LLM_PROVIDER=none installs)
    class InboundReading:  # type: ignore[no-redef]
        ALLOWED = ("intent", "promised_date", "confidence", "rationale")

        def __init__(self, **kw: Any):
            extra = sorted(set(kw) - set(self.ALLOWED))
            if extra:
                raise ValueError(f"fields outside the inbound schema: {extra}")
            if kw.get("intent") not in INTENTS:
                raise ValueError(f"intent {kw.get('intent')!r} outside the closed enum")
            conf = float(kw.get("confidence") or 0.0)
            if not 0.0 <= conf <= 1.0:
                raise ValueError("confidence out of range")
            self.intent = kw["intent"]
            self.promised_date = kw.get("promised_date")
            self.confidence = conf
            self.rationale = str(kw.get("rationale") or "")

        @classmethod
        def model_validate(cls, obj: dict[str, Any]) -> "InboundReading":
            return cls(**obj)

    _READING_ERRORS = (ValueError, TypeError, KeyError)


# The single assertion this whole design rests on, checked at import so it cannot rot.
_FORBIDDEN_FIELD_SUBSTRINGS = ("amount", "paise", "rupee", "rs", "price", "sum", "total")


def schema_fields() -> tuple[str, ...]:
    """The fields the inbound schema accepts. Used by the boundary test."""
    try:
        return tuple(InboundReading.model_fields)          # pydantic v2
    except AttributeError:
        return tuple(InboundReading.ALLOWED)               # fallback shim


def assert_no_money_field() -> None:
    """Raise if any money-shaped field ever appears in the inbound schema.

    Called at import. A boundary that is only tested is a boundary that holds until
    somebody runs the tests; one that refuses to import is a boundary.
    """
    for field in schema_fields():
        lowered = field.lower()
        for bad in _FORBIDDEN_FIELD_SUBSTRINGS:
            if bad in lowered.split("_") or lowered == bad or lowered.startswith(bad + "_"):
                raise AssertionError(
                    f"inbound schema field {field!r} looks like a money field. There is no "
                    "amount in an inbound reading, by design: a compromised model must not "
                    "be able to make this system state a wrong rupee figure, and the only "
                    "way to guarantee that is for the figure to have nowhere to travel."
                )


assert_no_money_field()


# ------------------------------------------------------------- date resolution
_WEEKDAYS = {
    "monday": 0, "mon": 0, "somvar": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "mangalvar": 1,
    "wednesday": 2, "wed": 2, "budhvar": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "guruvar": 3,
    "friday": 4, "fri": 4, "shukravar": 4,
    "saturday": 5, "sat": 5, "shanivar": 5,
    "sunday": 6, "sun": 6, "ravivar": 6,
}
# Hinglish relative days. `parso` means both "the day after tomorrow" and "the day
# before yesterday" depending on tense; forward is the only reading that makes sense
# for a promise, and a promise about the past is not a promise.
_RELATIVE_DAYS = {"aaj": 0, "today": 0, "kal": 1, "tomorrow": 1, "parso": 2}
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def resolve_date(text: Optional[str], *, now: Optional[datetime] = None) -> Optional[str]:
    """Turn what the customer said into an ISO date, or None.

    Accepts an ISO date directly (what the model is asked for) and, for the rule
    baseline, the Hinglish forms a person actually uses. Resolution is relative to the
    simulated clock, never to wall time, so a batch replay lands on the same dates.

    A date in the past, or beyond the episode window, resolves to None. Both are
    refusals rather than corrections: silently moving a promise to a date the customer
    did not name is exactly the invention this whole module exists to prevent.
    """
    if not text:
        return None
    now = now or clock.now()
    today = now.date()
    lowered = str(text).strip().lower()

    target = None
    m = _ISO_RE.search(lowered)
    if m:
        try:
            target = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    else:
        for word, offset in _RELATIVE_DAYS.items():
            if re.search(rf"\b{word}\b", lowered):
                target = today + timedelta(days=offset)
                break
        if target is None:
            for word, weekday in _WEEKDAYS.items():
                if re.search(rf"\b{word}\b", lowered):
                    ahead = (weekday - today.weekday()) % 7
                    target = today + timedelta(days=ahead or 7)
                    break

    if target is None:
        return None
    if target < today or (target - today).days > MAX_PROMISE_DAYS:
        return None
    return target.isoformat()


def normalise(reading: dict[str, Any], *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Apply the schema's own rules to a validated reading.

    A date on an undated intent is dropped, and `will_pay_on_date` without a resolvable
    date falls back to `unclear` — an intent that promises a day while naming none is
    not a promise anyone can track, and pretending otherwise would put an untrackable
    row in the promise table that could only ever resolve as broken.
    """
    intent = reading.get("intent")
    date = resolve_date(reading.get("promised_date"), now=now) if intent in DATED_INTENTS else None
    if intent == "will_pay_on_date" and date is None:
        intent, date = "unclear", None
    return {
        "intent": intent,
        "promised_date": date,
        "confidence": float(reading.get("confidence") or 0.0),
        "rationale": str(reading.get("rationale") or ""),
    }


# ------------------------------------------------------------ the rule baseline
# Written in good faith, not as a straw man: these are the phrases that actually carry
# an intent on an Indian dunning call, and the ablation is worth nothing if the
# baseline is built to lose. Where the model beats this, it beats a real attempt.
_RULE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("opt_out", ("do not call", "dont call", "don't call", "stop calling", "mat karo call",
                 "call mat", "unsubscribe", "remove my number", "band karo")),
    ("wrong_number", ("wrong number", "galat number", "who is this", "kaun bol raha",
                      "not my", "mera nahi")),
    ("disputes_charge", ("i did not", "maine nahi", "already paid", "kar diya payment",
                         "why am i being charged", "galat charge", "dispute", "cancel kar diya")),
    ("cannot_pay", ("cannot pay", "can not pay", "nahi kar sakta", "nahi kar paunga",
                    "no money", "paise nahi", "not able to pay")),
    ("will_pay_now", ("paying now", "abhi kar", "right now", "abhi karta", "abhi hi",
                      "doing it now")),
    ("will_pay_on_date", ("kar dunga", "kar dungi", "will pay", "pay kar", "by ", "ko kar",
                          "salary", "payday", "next week", "agle")),
    ("no_answer", ("[no answer]", "no answer", "voicemail", "not reachable")),
)


def read_by_rules(transcript: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """The keyword baseline the model is scored against (scripts/ablate_inbound.py)."""
    text = (transcript or "").strip().lower()
    if not text:
        return normalise({"intent": "no_answer", "confidence": 1.0,
                          "rationale": "empty transcript"}, now=now)
    for intent, patterns in _RULE_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                date = resolve_date(text, now=now) if intent in DATED_INTENTS else None
                return normalise({"intent": intent, "promised_date": date, "confidence": 1.0,
                                  "rationale": f"matched {pattern!r}"}, now=now)
    return normalise({"intent": "unclear", "confidence": 1.0, "rationale": "no pattern matched"},
                     now=now)


# --------------------------------------------------------------- the model path
SYSTEM_PROMPT = """You read what a customer said on a payment-recovery phone call in
India. The customer may speak English, Hindi, or Hinglish (Hindi written in Roman
letters, mixed with English).

Choose EXACTLY ONE intent from this fixed list:
- will_pay_on_date: they named a day they will pay on.
- will_pay_now: they are paying immediately.
- cannot_pay: they said they cannot pay.
- disputes_charge: they contest the charge, or say they already paid.
- wrong_number: they are not the customer.
- opt_out: they asked not to be contacted again.
- no_answer: nobody spoke, or the call was not answered.
- unclear: anything else.

Rules:
- If you are not confident, answer "unclear". Guessing is worse than admitting ignorance.
- NEVER report an amount, a price, or any sum of money. There is no field for one and
  you must not invent a place to put it.
- promised_date is a calendar date in YYYY-MM-DD format, and ONLY for will_pay_on_date.
  Today is {today}, which is a {weekday}. Resolve relative days against that.
- Reply with ONE JSON object and nothing else:
  {{"intent": "<one of the eight>", "promised_date": "<YYYY-MM-DD or null>",
    "confidence": <number between 0 and 1>, "rationale": "<one sentence>"}}
"""

USER_PROMPT = """The customer said:

{transcript}

JSON only."""


def read_by_model(transcript: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Ask the model. Every failure collapses to `unclear`, which promises nothing."""
    now = now or clock.now()
    if not llm.enabled():
        return normalise({"intent": "unclear", "confidence": 0.0,
                          "rationale": "no model configured"}, now=now)
    raw: Optional[str] = None
    try:
        raw = llm._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT.format(
                    today=now.date().isoformat(), weekday=now.strftime("%A"))},
                {"role": "user", "content": USER_PROMPT.format(transcript=transcript)},
            ],
            json_mode=True,
            temperature=0,
        )
        obj = llm.extract_json(raw)
        InboundReading.model_validate(obj)      # the closed schema, extras forbidden
        return normalise(obj, now=now)
    except Exception as exc:
        log.warning("inbound reading failed, collapsing to unclear: %s", exc)
        return normalise({"intent": "unclear", "confidence": 0.0,
                          "rationale": f"{type(exc).__name__}: {exc} | raw={raw}"}, now=now)


def read(transcript: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """The production path: the model when one is configured, the rules otherwise.

    Not a fallback chain — the rules are not a degraded model, they are a different
    reader with a measured accuracy of its own (scripts/ablate_inbound.py). Which one
    ran is recorded on the promise as `source`, so no row is ambiguous about it.
    """
    if llm.enabled():
        out = read_by_model(transcript, now=now)
        out["source"] = "llm"
        return out
    out = read_by_rules(transcript, now=now)
    out["source"] = "rule"
    return out


def policy_facts(reading: dict[str, Any]) -> tuple[str, Optional[str]]:
    """The only two things a reading is allowed to change: whether a promise exists,
    and what day it is due.

    The ablation scores this pair rather than the raw fields, because it is the column
    that matters — two readings that disagree about wording but produce the same
    scheduling decision have not disagreed about anything the system does.
    """
    intent = reading.get("intent")
    return (
        "promise" if intent == "will_pay_on_date" else
        "suppress" if intent in SUPPRESSING_INTENTS else
        "handoff" if intent in {"cannot_pay", "disputes_charge"} else
        "none",
        reading.get("promised_date"),
    )


def to_json(reading: dict[str, Any]) -> str:
    return json.dumps(reading, sort_keys=True, default=str)
