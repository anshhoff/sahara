"""Every tunable constant, the rule table, the policy table and the static copy
templates. Nothing here is changeable at runtime: the bounds of the agent are
source code, not configuration (docs/04 header).

Env vars are read once at import. See docs/10 §10 for the complete list.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional; the app runs without it if .env is absent
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv missing is not fatal
    pass

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------- bounds
MAX_ATTEMPTS = 3
COOLDOWN_HOURS = 24  # minimum gap between two customer contacts on one case (I2)
EPISODE_WINDOW_DAYS = 14  # an open case older than this is closed, never left a zombie
LLM_CONFIDENCE_THRESHOLD = 0.8
PROMISE_WINDOW_HOURS = 72  # promise-to-pay grace before handoff

# ------------------------------------------------------- contact-time bounds
# India-specific and deliberately conservative. TRAI's commercial-communication
# framework restricts promotional messaging to daytime hours; transactional dunning
# sits in a softer category, so 09:00-21:00 IST is stricter than the letter of the
# rule rather than an attempt to sit exactly on it. IST is UTC+05:30 with no DST,
# which is why a fixed offset is correct here and a timezone database is not needed.
IST_OFFSET_MINUTES = 330
QUIET_HOURS_START_IST = 21  # no customer contact from 21:00 ...
QUIET_HOURS_END_IST = 9     # ... until 09:00 the next morning (I5)

# On by default everywhere, including the test suite (tests/test_contact_hygiene.py
# pins I5's behavior). The one legitimate reason to flip this off is walking a live
# demo through multiple attempts at night without waiting out real IST hours — a
# demo-only override, not a relaxed default, which is why it takes an explicit env
# var rather than a lower default.
QUIET_HOURS_ENABLED = _env_bool("QUIET_HOURS_ENABLED", True)

# I7. Two independent daily ceilings, both reset at IST midnight.
#
# I2 already spaces contacts 24h apart WITHIN one case. Neither of these is a
# duplicate of it: MAX_CONTACTS_PER_CUSTOMER_PER_DAY spans a customer's *other*
# subscriptions, which I2 cannot see, and DAILY_OUTREACH_BUDGET_PAISE is a
# system-wide spend ceiling that binds no matter how many customers are involved.
MAX_CONTACTS_PER_CUSTOMER_PER_DAY = 2
DAILY_OUTREACH_BUDGET_PAISE = int(_env("DAILY_OUTREACH_BUDGET_PAISE", "500000"))  # Rs 5,000

# ------------------------------------------------- I8, verified recipients
# The allowlist for a REAL outbound transmission. Empty by default, which is the
# safe default: with nothing on the list, nothing can be dialled.
#
# This is deliberately an env var of E.164 numbers and not a database table. A
# database row can be written by any code path that can write rows, including a future
# one nobody has reviewed; an env var is set by the operator of the process, once,
# outside the application. The bound of the agent should be harder to change than the
# agent.
VERIFIED_RECIPIENTS = frozenset(
    n.strip() for n in _env("VERIFIED_RECIPIENTS", "").split(",") if n.strip()
)

# Real transmission is OFF unless explicitly switched on. Everything in the batch and
# the test suite runs with this false, which is why I8 is a structural boundary rather
# than a caveat: with no real-send path enabled there is nothing to allowlist against,
# and with it enabled a synthetic customer has no verified number to match.
VOICE_REAL_SEND_ENABLED = _env_bool("VOICE_REAL_SEND_ENABLED", False)

# --------------------------------------------------------------------- enums
CATEGORIES = (
    "card_expired",
    "insufficient_funds",
    "issuer_declined",
    "authentication_failed",
    "invalid_payment_method",
    "unknown",
)
# VOICE_CALL is registered as an ACTION, not as a channel on an existing action, and
# that is the entire design argument. Every guardrail in invariants.py keys off
# CONTACT_ACTIONS and ACTION_COST_PAISE; adding voice as a member of those sets makes
# it inherit I2's cooldown, I5's quiet hours, I6's suppression, I7's ceilings and
# annoyance pricing with ZERO new guardrail code. A "channel" flag on SEND_UPDATE_LINK
# would have needed each of those gates taught about it separately, and the one that
# got forgotten would be the one that called somebody at 2am.
ACTIONS = ("RETRY_LATER", "SEND_UPDATE_LINK", "PROMISE_TO_PAY", "VOICE_CALL", "STOP_HANDOFF")

# Actions that put a message in front of a human being. RETRY_LATER is a silent
# re-charge of an existing mandate, not a contact, so the cooldown (I2) does not
# apply to it — a deliberate and defensible compliance distinction (docs/04 §5).
CONTACT_ACTIONS = frozenset({"SEND_UPDATE_LINK", "PROMISE_TO_PAY", "VOICE_CALL"})

# The same set as a SQL literal, DERIVED rather than retyped. Half a dozen queries —
# I7's daily count, the I2/I5/I7 acceptance checks, the fencing breach query — used to
# spell the tuple out by hand, which meant adding VOICE_CALL to CONTACT_ACTIONS made
# it inherit the gates in Python while every SQL query quietly went on ignoring it.
# A set that can drift from its own SQL is a set with two definitions.
CONTACT_ACTIONS_SQL = "(" + ", ".join(f"'{a}'" for a in sorted(CONTACT_ACTIONS)) + ")"

CASE_STATUSES = (
    "open",
    "recovered",
    "stopped_max_attempts",
    "stopped_cooldown_expired",
    "stopped_opt_out",
    "stopped_unknown",
    "stopped_handoff",
    # The economics said no: the expected value of the next intervention was
    # negative, so the cheapest correct action was to not send it (gate E1, economics.py).
    "stopped_uneconomic",
    # The customer is on the suppression list — an opt-out recorded against the
    # PERSON, not this one case, so it reaches their other subscriptions too (I6).
    "stopped_suppressed",
    # A control-arm case: deliberately never intervened on, so the treated arm has
    # something to be measured against. Not a failure and not a safety stop.
    "stopped_holdout",
    # A dispatch fence found the money had already arrived between deciding and
    # acting (app/fencing.py). This is a CORRECTNESS stop rather than a safety one:
    # nothing forbade the message, there was simply nothing left to collect.
    "stopped_already_settled",
    # I8 failing closed: a real transmission was proposed to a destination that is not
    # on the verified-recipient allowlist, so it refused rather than dialled.
    "stopped_unverified_recipient",
)
TERMINAL_STATUSES = tuple(s for s in CASE_STATUSES if s != "open")
STOPPED_STATUSES = tuple(s for s in CASE_STATUSES if s.startswith("stopped_"))

FAILURE_EVENT_TYPES = ("payment.failed", "subscription.pending", "subscription.halted")
# `order.paid` is here because of a hard test-mode ceiling, not a design preference:
# a test-mode account may create only 30 Payment Links, ever, and this account has
# spent all 30 (see UPDATE_LINK_MODE). Orders have no such ceiling, so the update-link
# rung falls back to a real Order + hosted Checkout, whose recovery signal arrives as
# `order.paid` rather than `payment_link.paid`. Same money, same case, different event
# name — `webhooks.extract()` already recovers the case from `notes.case_id`, which
# Razorpay copies from the order onto the payment.
RECOVERY_EVENT_TYPES = ("subscription.charged", "payment_link.paid", "order.paid")

# ------------------------------------------------------------------- database
DB_PATH = _env("DB_PATH", str(ROOT / "recovery.db"))
SCHEMA_PATH = str(ROOT / "schema.sql")

# ------------------------------------------------------------------- razorpay
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
LIVE_LINKS_MAX = int(_env("LIVE_LINKS_MAX", "5"))

# Which surface SEND_UPDATE_LINK uses to produce something the customer can actually pay.
#
#   payment_link — Razorpay Payment Links. The nicest artefact (hosted page, short_url)
#                  and the one that runs out: test mode caps an account at 30 for the
#                  lifetime of the key, after which every create returns
#                  `ServerError: test mode limit of 30 reached for payment_link` and the
#                  executor honestly records a `https://example.invalid/...` stand-in.
#   order        — a real Order plus the console's own /pay page running Razorpay
#                  Checkout. No ceiling; payable in test mode with `success@razorpay`.
#   auto         — try Payment Links while budget and quota last, fall back to Order,
#                  fall back to the simulated stand-in. The default, because a demo
#                  should degrade to something payable before it degrades to a fiction.
#
# The fallback ladder never silently upgrades honesty: whichever rung produced the URL
# is recorded as the execution `mode`, and the console renders the difference.
UPDATE_LINK_MODE = _env("UPDATE_LINK_MODE", "auto")

# The Order rung's own budget. Separate from LIVE_LINKS_MAX and larger, because the two
# rungs consume different resources: Payment Links are the thing an account runs out of
# permanently, Orders are ordinary rate-limited API calls. Both are still real calls on
# a real key, so both are budgeted and both are zeroed in tests.
LIVE_ORDERS_MAX = int(_env("LIVE_ORDERS_MAX", "50"))

# Where the console is served from, so an Order-backed link points at a page that exists.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/")

# The dashboard's control room runs the test suite, the batch and adversarial
# storms in-process or as subprocesses. It is a demo surface on a single-operator
# local app, and it is unauthenticated like the rest of the API, so it must be
# switchable off in one place before this is ever bound to anything but localhost.
CONTROL_ENABLED = _env_bool("CONTROL_API_ENABLED", True)

# ------------------------------------------------------------- public demo
# A deployment anyone can click. Off by default, because the safe default for a flag
# that changes what the system is allowed to do is the one that changes nothing.
#
# Two things it does, and the second is the one that matters:
#
#  1. Every write route is disabled. Not hidden — a POST returns 404, so there is no
#     endpoint to find rather than an endpoint that says no.
#  2. **Boot REFUSES if any provider credential is present.** A public demo that could
#     be handed a Razorpay key and start moving money by env var is not fail-closed; it
#     is fail-closed-until-somebody-changes-their-mind. `assert_demo_safe()` runs at
#     startup and exits non-zero, so the container dies instead of serving.
#
# The second is why this is a code path and not a Dockerfile line. A container is a
# deployment detail; the bounds of the agent are source code.
PUBLIC_DEMO = _env_bool("PUBLIC_DEMO", False)

# Credentials whose mere PRESENCE contradicts a public demo. Not their validity —
# checking whether a key works would mean using it, which is precisely what must not
# happen here.
_DEMO_FORBIDDEN_ENV = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "PLIVO_AUTH_ID",
    "PLIVO_AUTH_TOKEN",
)


def demo_violations() -> list[str]:
    """Every reason this process must not run as a public demo. Empty means it may."""
    if not PUBLIC_DEMO:
        return []
    problems = [f"{name} is set" for name in _DEMO_FORBIDDEN_ENV if os.environ.get(name)]
    if VOICE_REAL_SEND_ENABLED:
        problems.append("VOICE_REAL_SEND_ENABLED is true")
    if VERIFIED_RECIPIENTS:
        problems.append(f"VERIFIED_RECIPIENTS lists {len(VERIFIED_RECIPIENTS)} number(s)")
    if CONTROL_ENABLED:
        problems.append("CONTROL_API_ENABLED is true — the control room runs subprocesses")
    return problems


def assert_demo_safe() -> None:
    """Raise if this process claims to be a public demo and is not one.

    Called from the startup hook. The failure mode this exists to prevent is a
    deployment that is *mostly* a demo — read-only routes, seeded data, and one live
    credential somebody added to test something and left behind.
    """
    problems = demo_violations()
    if problems:
        raise RuntimeError(
            "PUBLIC_DEMO=true but this process holds live capability: "
            + "; ".join(problems)
            + ". Refusing to start. A demo that can be handed a credential is not a demo."
        )
MERCHANT_NAME = _env("MERCHANT_NAME", "Demo Subscriptions")

# ------------------------------------------------------------------------ LLM
# Provider modes (docs/10 §5.1):
#   PROVIDER_LOCAL  -> a local open-weights model served by Ollama (default, free, offline)
#   PROVIDER_HOSTED -> any OpenAI-compatible free tier (Groq / OpenRouter / Google AI Studio)
#   PROVIDER_NONE   -> no model at all; ambiguous cases become `unknown` and stop
PROVIDER_LOCAL = "ollama"
PROVIDER_HOSTED = "openai_compat"
PROVIDER_NONE = "none"

LLM_PROVIDER = _env("LLM_PROVIDER", PROVIDER_LOCAL).strip().lower()
if LLM_PROVIDER in {"compat", "hosted"}:
    LLM_PROVIDER = PROVIDER_HOSTED
LLM_BASE_URL = _env("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = _env("LLM_MODEL", "qwen2.5:7b-instruct")
LLM_API_KEY = _env("LLM_API_KEY", "ollama")
LLM_TIMEOUT_S = int(_env("LLM_TIMEOUT_S", "20"))
LLM_COPY_ENABLED = _env_bool("LLM_COPY_ENABLED", True)

# ------------------------------------------------------------------ rule table
# docs/04 §3.2. Checked first, in order; first hit wins. Match input is the
# concatenation of error_reason + error_description + error_code, lowercased.
# Patterns are plain lowercase substrings (no regex) so the table stays readable
# and cannot surprise anyone with backtracking behaviour.
RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # R1 is deliberately narrow: a bare "expired" also appears in "OTP expired",
    # which belongs to R3. Every pattern here names the card explicitly.
    ("R1", ("card_expired", "expired card", "card expired", "card has expired",
            "card is expired", "expired_card", "card expiry", "expiry date"),
     "card_expired"),
    ("R2", ("insufficient", "low balance", "exceeds limit", "exceeds the limit", "insufficient_funds",
            "not enough balance"),
     "insufficient_funds"),
    ("R3", ("authentication", "3dsecure", "3d secure", "3ds", "otp", "mandate authentication",
            "authentication_failed"),
     "authentication_failed"),
    ("R4", ("invalid card", "card blocked", "blocked card", "card lost", "card stolen", "invalid_vpa",
            "account closed", "mandate cancelled", "mandate revoked", "invalid_payment_method"),
     "invalid_payment_method"),
    ("R5", ("do_not_honour", "do not honour", "issuer declined", "declined by",
            "transaction declined", "was declined", "payment_declined_by_bank"),
     "issuer_declined"),
    # R6: transient bank/gateway problems. Retry-once semantics fit, so they share
    # the issuer_declined policy row rather than getting a category of their own.
    ("R6", ("payment timed out", "gateway technical error", "gateway_error", "timed out",
            "technical error"),
     "issuer_declined"),
)
RULE_R7 = "R7"  # all error fields empty/null -> unknown, without troubling the LLM

# ---------------------------------------------------------------- policy table
# docs/04 §4.1. (category, attempt_number) -> (action, delay_hours).
#
# Two design notes the panel will ask about:
#  * card_expired and invalid_payment_method never RETRY_LATER: retrying a dead
#    instrument burns one of three attempts and annoys the issuer. The fix is a
#    new instrument, i.e. a link.
#  * Contact actions carry delay 0. The 24h spacing between two contacts is NOT a
#    policy delay — it is invariant I2, enforced in invariants.py, which defers the
#    execution. Spacing therefore holds even if this table is wrong.
POLICY: dict[tuple[str, int], tuple[str, int]] = {
    ("card_expired", 1): ("SEND_UPDATE_LINK", 0),
    ("card_expired", 2): ("SEND_UPDATE_LINK", 0),   # reminder; I2 defers it past cooldown
    ("card_expired", 3): ("VOICE_CALL", 0),         # was STOP_HANDOFF; see the note below

    ("insufficient_funds", 1): ("RETRY_LATER", 24),
    ("insufficient_funds", 2): ("RETRY_LATER", 72),  # salary-cycle spacing
    ("insufficient_funds", 3): ("PROMISE_TO_PAY", 0),

    ("issuer_declined", 1): ("RETRY_LATER", 12),     # transient declines clear
    ("issuer_declined", 2): ("SEND_UPDATE_LINK", 0),
    ("issuer_declined", 3): ("VOICE_CALL", 0),

    ("authentication_failed", 1): ("SEND_UPDATE_LINK", 0),  # fresh auth link
    ("authentication_failed", 2): ("SEND_UPDATE_LINK", 0),
    ("authentication_failed", 3): ("VOICE_CALL", 0),

    ("invalid_payment_method", 1): ("SEND_UPDATE_LINK", 0),
    ("invalid_payment_method", 2): ("STOP_HANDOFF", 0),
    ("invalid_payment_method", 3): ("STOP_HANDOFF", 0),  # unreachable; keeps the table total

    # Voice SUBSTITUTES at attempt 3; it is not an added fourth rung. Before this,
    # card_expired, issuer_declined and authentication_failed all jumped straight from
    # a silent link to occupying a person at Rs 40. Voice fills that rung at Rs 25, and
    # a call that goes unanswered or whose promise lapses still closes the case as
    # `stopped_handoff` — so the human queue remains the floor, not the third step.
    #
    # MAX_ATTEMPTS stays 3 and I1 is untouched. STOP_HANDOFF was never an intervention:
    # it is in economics.NON_INTERVENTION_ACTIONS and never consumed an attempt slot, so
    # there was a free rung here all along.

    # Defence in depth only: I4 stops an `unknown` case before the policy is consulted.
    ("unknown", 1): ("STOP_HANDOFF", 0),
    ("unknown", 2): ("STOP_HANDOFF", 0),
    ("unknown", 3): ("STOP_HANDOFF", 0),
}

# ------------------------------------------------------------ copy constraints
SYNTHETIC_DISCLOSURE = "[SYNTHETIC DEMO]"
LINK_PLACEHOLDER = "{LINK}"
AMOUNT_PLACEHOLDER = "{AMOUNT}"
COPY_MAX_CHARS = 320
COPY_FORBIDDEN = ("refund", "guarantee", "legal", "penalty", "last chance")

# Copy is written as a SLOT SKELETON: drafted text may contain no digit of its own,
# only these placeholders, which deterministic code substitutes with authoritative
# values afterwards. The earlier design let a draft write the amount as digits and
# then checked it matched, which rejected every otherwise-good sentence that
# mentioned any other number ("within 24 hours", "attempt 2 of 3"). Slots are
# strictly safer — a model that cannot type a digit cannot invent one — and they
# let copy cite the bounds the system actually enforces.
COPY_SLOTS: tuple[str, ...] = (
    LINK_PLACEHOLDER,
    AMOUNT_PLACEHOLDER,
    "{MERCHANT}",
    "{COOLDOWN_HOURS}",
    "{PROMISE_HOURS}",
)

# Static templates exist for every (category, contact-action) pair, so the system is
# fully functional with the copy LLM switched off (LLM_COPY_ENABLED=false or
# LLM_PROVIDER=none). The only number any template may contain is the amount —
# the copy validator enforces that, including against these templates.
_LINK = LINK_PLACEHOLDER
_D = SYNTHETIC_DISCLOSURE
STATIC_TEMPLATES: dict[tuple[str, str], str] = {
    ("card_expired", "SEND_UPDATE_LINK"):
        f"{_D} Your subscription payment of Rs {{AMOUNT}} could not be completed because the card on "
        f"file has expired. Add a current card here: {_LINK}",
    ("card_expired", "PROMISE_TO_PAY"):
        f"{_D} Your subscription payment of Rs {{AMOUNT}} is still pending. Tell us when you can pay "
        f"and settle it here: {_LINK}",

    ("insufficient_funds", "SEND_UPDATE_LINK"):
        f"{_D} We could not collect Rs {{AMOUNT}} for your subscription. You can pay it directly "
        f"here: {_LINK}",
    ("insufficient_funds", "PROMISE_TO_PAY"):
        f"{_D} We have tried collecting Rs {{AMOUNT}} for your subscription without success. Pay "
        f"within three days to keep it active: {_LINK}",

    ("issuer_declined", "SEND_UPDATE_LINK"):
        f"{_D} Your bank declined the subscription charge of Rs {{AMOUNT}}. You can complete the "
        f"payment or use another method here: {_LINK}",
    ("issuer_declined", "PROMISE_TO_PAY"):
        f"{_D} The charge of Rs {{AMOUNT}} was declined by your bank. Settle it within three days "
        f"here: {_LINK}",

    ("authentication_failed", "SEND_UPDATE_LINK"):
        f"{_D} The authentication for your subscription payment of Rs {{AMOUNT}} did not complete. "
        f"Here is a fresh secure link: {_LINK}",
    ("authentication_failed", "PROMISE_TO_PAY"):
        f"{_D} Your subscription payment of Rs {{AMOUNT}} is still unauthenticated. Complete it "
        f"within three days: {_LINK}",

    ("invalid_payment_method", "SEND_UPDATE_LINK"):
        f"{_D} The payment method saved for your subscription is no longer usable, so Rs {{AMOUNT}} "
        f"could not be collected. Add a new one here: {_LINK}",
    ("invalid_payment_method", "PROMISE_TO_PAY"):
        f"{_D} We still could not collect Rs {{AMOUNT}} for your subscription. Add a working payment "
        f"method within three days: {_LINK}",

    ("unknown", "SEND_UPDATE_LINK"):
        f"{_D} A payment of Rs {{AMOUNT}} for your subscription did not go through. You can "
        f"complete it here: {_LINK}",
    ("unknown", "PROMISE_TO_PAY"):
        f"{_D} A payment of Rs {{AMOUNT}} for your subscription is outstanding. You can settle it "
        f"here: {_LINK}",
}

# ------------------------------------------------------------ voice templates
# A voice call is an outbound string like any other, so it goes through the identical
# validator: the synthetic disclosure, exactly one {LINK}, no typed digit anywhere, no
# forbidden word, and the same length ceiling. Registering the language variants here
# rather than composing them at call time is what puts them behind that gate — an
# unregistered variant cannot be spoken, and tests/test_copy_validation.py fails the
# build if any registered one would not pass.
#
# The digit rule holds across scripts: Python's \d matches Devanagari ०-९ as well as
# ASCII, so a Hindi draft cannot smuggle in an amount either.
#
# Hinglish is romanised Hindi, not a third language — it is what an Indian customer
# service call actually sounds like, and it is the register in which someone says
# "salary aane ke baad Friday ko kar dunga".
VOICE_LANGUAGES = ("en", "hi", "hi_en")
VOICE_DEFAULT_LANGUAGE = "hi_en"

_CALL_EN = f"{_D} Automated call from {{MERCHANT}}."
_CALL_HI = f"{_D} {{MERCHANT}} की ओर से स्वचालित कॉल।"
_CALL_HE = f"{_D} {{MERCHANT}} ki taraf se automated call."

VOICE_TEMPLATES: dict[tuple[str, str], str] = {
    ("card_expired", "en"):
        f"{_CALL_EN} Your subscription payment of Rs {{AMOUNT}} could not be collected "
        f"because the card on file has expired. Please add a current card here: {_LINK}",
    ("card_expired", "hi"):
        f"{_CALL_HI} आपके सब्सक्रिप्शन का Rs {{AMOUNT}} का भुगतान नहीं हो सका, क्योंकि कार्ड की "
        f"वैधता समाप्त हो चुकी है। कृपया नया कार्ड यहाँ जोड़ें: {_LINK}",
    ("card_expired", "hi_en"):
        f"{_CALL_HE} Aapke subscription ka Rs {{AMOUNT}} ka payment nahi ho paya kyunki "
        f"card expire ho chuka hai. Naya card yahan add karein: {_LINK}",

    ("insufficient_funds", "en"):
        f"{_CALL_EN} We could not collect Rs {{AMOUNT}} for your subscription. Tell us "
        f"when you can pay, or settle it here: {_LINK}",
    ("insufficient_funds", "hi"):
        f"{_CALL_HI} आपके सब्सक्रिप्शन के लिए Rs {{AMOUNT}} नहीं लिए जा सके। बताइए आप कब भुगतान "
        f"कर सकते हैं, या यहाँ भुगतान करें: {_LINK}",
    ("insufficient_funds", "hi_en"):
        f"{_CALL_HE} Aapke subscription ke liye Rs {{AMOUNT}} collect nahi ho paye. Bataiye "
        f"aap kab pay kar sakte hain, ya yahan pay karein: {_LINK}",

    ("issuer_declined", "en"):
        f"{_CALL_EN} Your bank declined the subscription charge of Rs {{AMOUNT}}. You can "
        f"complete the payment or use another method here: {_LINK}",
    ("issuer_declined", "hi"):
        f"{_CALL_HI} आपके बैंक ने Rs {{AMOUNT}} का सब्सक्रिप्शन शुल्क अस्वीकार कर दिया। आप यहाँ "
        f"भुगतान पूरा कर सकते हैं या दूसरा तरीका चुन सकते हैं: {_LINK}",
    ("issuer_declined", "hi_en"):
        f"{_CALL_HE} Aapke bank ne Rs {{AMOUNT}} ka subscription charge decline kar diya. "
        f"Yahan payment complete karein ya doosra method chunein: {_LINK}",

    ("authentication_failed", "en"):
        f"{_CALL_EN} The authentication for your subscription payment of Rs {{AMOUNT}} did "
        f"not complete. Here is a fresh secure link: {_LINK}",
    ("authentication_failed", "hi"):
        f"{_CALL_HI} आपके Rs {{AMOUNT}} के सब्सक्रिप्शन भुगतान का प्रमाणीकरण पूरा नहीं हुआ। यह "
        f"नया सुरक्षित लिंक है: {_LINK}",
    ("authentication_failed", "hi_en"):
        f"{_CALL_HE} Aapke Rs {{AMOUNT}} ke subscription payment ka authentication complete "
        f"nahi hua. Yeh naya secure link hai: {_LINK}",

    ("invalid_payment_method", "en"):
        f"{_CALL_EN} The payment method saved for your subscription is no longer usable, so "
        f"Rs {{AMOUNT}} could not be collected. Add a new one here: {_LINK}",
    ("invalid_payment_method", "hi"):
        f"{_CALL_HI} आपके सब्सक्रिप्शन में सहेजा गया भुगतान तरीका अब काम नहीं करता, इसलिए "
        f"Rs {{AMOUNT}} नहीं लिए जा सके। नया तरीका यहाँ जोड़ें: {_LINK}",
    ("invalid_payment_method", "hi_en"):
        f"{_CALL_HE} Aapke subscription ka saved payment method ab kaam nahi karta, isliye "
        f"Rs {{AMOUNT}} collect nahi ho paye. Naya method yahan add karein: {_LINK}",

    # `unknown` never reaches a contact action — I4 stops the case before the policy is
    # consulted. Registered anyway so the table is TOTAL over the six categories, for
    # the same reason POLICY is: a lookup that can fall through to a default is a
    # lookup that will, on the one input nobody thought about.
    ("unknown", "en"):
        f"{_CALL_EN} A payment of Rs {{AMOUNT}} for your subscription did not go through. "
        f"You can complete it here: {_LINK}",
    ("unknown", "hi"):
        f"{_CALL_HI} आपके सब्सक्रिप्शन का Rs {{AMOUNT}} का भुगतान पूरा नहीं हुआ। आप इसे यहाँ "
        f"पूरा कर सकते हैं: {_LINK}",
    ("unknown", "hi_en"):
        f"{_CALL_HE} Aapke subscription ka Rs {{AMOUNT}} ka payment complete nahi hua. Aap "
        f"ise yahan complete kar sakte hain: {_LINK}",
}

# The English variant doubles as the (category, VOICE_CALL) entry, so voice needs no
# special case anywhere in executor.static_template() and inherits the exhaustiveness
# test that covers every other contact action.
STATIC_TEMPLATES.update({
    (category, "VOICE_CALL"): text
    for (category, lang), text in VOICE_TEMPLATES.items() if lang == "en"
})

# Placeholder link used when an execution is simulated rather than a real test-mode
# Payment Link. `.invalid` is reserved by RFC 2606 and can never resolve.
SIMULATED_LINK_BASE = "https://example.invalid/pay/"


# ------------------------------------------------------------------ economics
# What an intervention COSTS, so that recovery can be reported net rather than
# gross. Every constant below is an assumption with a stated rationale and no
# measurement behind it — the same honesty that applies to the batch's outcome
# model applies here, and the README says so in the same words.
#
# The point of pricing an intervention is not the arithmetic. It is that a system
# which only counts rupees recovered will always conclude that one more message is
# free, and it is not: it costs a fraction of a rupee to send and some probability
# of the customer cancelling outright.

# Direct, per-execution cost.
#  * RETRY_LATER is a silent re-charge of an existing mandate. It contacts nobody
#    and costs nothing to attempt, which is exactly why the policy table reaches
#    for it first wherever the instrument might still work.
#  * A contact is priced fully loaded, not at the wire cost of an SMS: Rs 0.25 to
#    send, plus roughly a 4% chance of provoking an inbound support contact worth
#    about Rs 300 of somebody's time. Rs 0.25 + 0.04 x Rs 300 = Rs 12.25, rounded.
#  * STOP_HANDOFF moves no money and sends nothing, but it is not free — it puts a
#    case in a human queue. Counting it is what stops "hand it off" from looking
#    like a costless way to make a hard case disappear.
#  * VOICE_CALL is priced between the two, at Rs 25: more than a link because a call
#    costs real telephony minutes and carries a higher chance of an inbound follow-up,
#    less than a handoff because it does not occupy a person for the length of a case.
#    Rs 25 against Rs 40 is the whole economic argument for the rung — and because the
#    EV gate reads this table, a voice call that is not worth Rs 25 is simply not made.
ACTION_COST_PAISE: dict[str, int] = {
    "RETRY_LATER": 0,
    "SEND_UPDATE_LINK": 1200,
    "PROMISE_TO_PAY": 1200,
    "VOICE_CALL": 2500,
    "STOP_HANDOFF": 4000,
}

# The cost that does not appear on any invoice: each successive unsolicited payment
# message in one episode carries a chance the customer cancels rather than pays.
# Indexed by attempt number, escalating — the third message is more irritating than
# the first, not equally so.
CONTACT_CHURN_HAZARD: tuple[float, ...] = (0.004, 0.010, 0.020)
# One hazard curve for every channel, deliberately. A per-channel multiplier for voice
# was drafted and removed: a call is more intrusive to receive, and it is also the only
# contact where the customer can object and be answered, so the sign of the difference
# is genuinely unclear. An assumption with no basis and a direct effect on which
# interventions fire is worse than no assumption.

# What a cancellation costs, expressed as months of the failed charge. One charge
# cycle is what the case is worth today; the subscription behind it is worth more.
# Twelve is a deliberately modest horizon: a longer one would inflate the annoyance
# term and make the agent look more restrained than its evidence supports.
LTV_HORIZON_MONTHS = 12

# The agent's OWN belief about how often an intervention works, by (category,
# action) and attempt number.
#
# This table must never be reconciled with scripts/run_batch.py's SUCCESS_PROBABILITY,
# which is the simulated world's ground truth. They are two different objects: this
# is what the agent believes before acting, that is what actually happens. If they
# were equal the agent would be scoring its decisions with the answer key, every EV
# would be correct by construction, and the whole measurement would be circular.
# tests/test_economics.py asserts they are not equal, and that nothing in app/
# imports the simulator.
#
# The ordering is the defensible part, not the exact values: a first touch converts
# best, reminders decay, and a promise-to-pay holds its rate because it is agreed
# with the customer rather than pushed at them.
P_RECOVER_PRIOR: dict[tuple[str, str], tuple[float, float, float]] = {
    ("card_expired", "SEND_UPDATE_LINK"): (0.36, 0.22, 0.14),
    ("insufficient_funds", "RETRY_LATER"): (0.42, 0.32, 0.24),
    ("insufficient_funds", "PROMISE_TO_PAY"): (0.46, 0.46, 0.46),
    ("insufficient_funds", "SEND_UPDATE_LINK"): (0.32, 0.18, 0.12),
    ("issuer_declined", "RETRY_LATER"): (0.28, 0.22, 0.16),
    ("issuer_declined", "SEND_UPDATE_LINK"): (0.30, 0.22, 0.14),
    ("authentication_failed", "SEND_UPDATE_LINK"): (0.42, 0.24, 0.16),
    ("invalid_payment_method", "SEND_UPDATE_LINK"): (0.26, 0.16, 0.10),

    # Voice, at the third rung. Higher than a third silent message for the reason a
    # call is worth more than a text — it is answered or it is not, and an answered one
    # can hear an objection and take a dated promise, which no SMS can do. Not higher
    # than a FIRST link, because by attempt 3 the easy cases are already gone; a prior
    # that ignored that would let the EV gate wave through calls it should refuse.
    # Voice holds its rate across attempts, like PROMISE_TO_PAY and unlike every push
    # channel, and for the same reason: its OUTPUT IS AN AGREEMENT. A third SMS is a
    # third time we said something; an answered call ends with the customer naming a
    # day, and a date somebody chose converts differently from a deadline we imposed.
    # That is the defensible claim here — the shape, not the third decimal.
    #
    # It matters more than it looks. The annoyance term at attempt 3 is
    # 0.020 x 12 = 0.24 of the amount, so ANY third contact whose prior sits below 0.24
    # is uneconomic at every ticket size, because the amount cancels out of both sides.
    # A decaying voice prior would mean the rung could never fire and the whole rung
    # would be decorative.
    ("card_expired", "VOICE_CALL"): (0.38, 0.36, 0.34),
    ("issuer_declined", "VOICE_CALL"): (0.34, 0.32, 0.30),
    ("authentication_failed", "VOICE_CALL"): (0.40, 0.38, 0.36),
    ("insufficient_funds", "VOICE_CALL"): (0.42, 0.40, 0.38),
}
# Anything the table does not name is assumed to work poorly rather than averagely.
# An unlisted pair is a pair nobody reasoned about, and the safe reading of a cell
# nobody reasoned about is a pessimistic one.
P_RECOVER_DEFAULT: tuple[float, float, float] = (0.20, 0.12, 0.08)

# Which terminal states put a case in front of a person, and therefore incur the
# STOP_HANDOFF cost above. Deliberately not "everything that did not recover":
#
#   * opt-out and suppression are the customer's decision, and there is nothing for a
#     human to work on — acting further is precisely what was forbidden;
#   * a holdout case was never worked by anyone, by construction;
#   * an uneconomic case was closed because pursuing it costs more than it returns,
#     and charging it a human's time would contradict the decision that closed it.
HANDOFF_STATUSES = (
    "stopped_handoff",
    "stopped_max_attempts",
    "stopped_cooldown_expired",
    "stopped_unknown",
)
