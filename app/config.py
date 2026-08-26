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

# --------------------------------------------------------------------- enums
CATEGORIES = (
    "card_expired",
    "insufficient_funds",
    "issuer_declined",
    "authentication_failed",
    "invalid_payment_method",
    "unknown",
)
ACTIONS = ("RETRY_LATER", "SEND_UPDATE_LINK", "PROMISE_TO_PAY", "STOP_HANDOFF")

# Actions that put a message in front of a human being. RETRY_LATER is a silent
# re-charge of an existing mandate, not a contact, so the cooldown (I2) does not
# apply to it — a deliberate and defensible compliance distinction (docs/04 §5).
CONTACT_ACTIONS = frozenset({"SEND_UPDATE_LINK", "PROMISE_TO_PAY"})

CASE_STATUSES = (
    "open",
    "recovered",
    "stopped_max_attempts",
    "stopped_cooldown_expired",
    "stopped_opt_out",
    "stopped_unknown",
    "stopped_handoff",
)
TERMINAL_STATUSES = tuple(s for s in CASE_STATUSES if s != "open")
STOPPED_STATUSES = tuple(s for s in CASE_STATUSES if s.startswith("stopped_"))

FAILURE_EVENT_TYPES = ("payment.failed", "subscription.pending", "subscription.halted")
RECOVERY_EVENT_TYPES = ("subscription.charged", "payment_link.paid")

# ------------------------------------------------------------------- database
DB_PATH = _env("DB_PATH", str(ROOT / "recovery.db"))
SCHEMA_PATH = str(ROOT / "schema.sql")

# ------------------------------------------------------------------- razorpay
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
LIVE_LINKS_MAX = int(_env("LIVE_LINKS_MAX", "5"))
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
    ("card_expired", 3): ("STOP_HANDOFF", 0),

    ("insufficient_funds", 1): ("RETRY_LATER", 24),
    ("insufficient_funds", 2): ("RETRY_LATER", 72),  # salary-cycle spacing
    ("insufficient_funds", 3): ("PROMISE_TO_PAY", 0),

    ("issuer_declined", 1): ("RETRY_LATER", 12),     # transient declines clear
    ("issuer_declined", 2): ("SEND_UPDATE_LINK", 0),
    ("issuer_declined", 3): ("STOP_HANDOFF", 0),

    ("authentication_failed", 1): ("SEND_UPDATE_LINK", 0),  # fresh auth link
    ("authentication_failed", 2): ("SEND_UPDATE_LINK", 0),
    ("authentication_failed", 3): ("STOP_HANDOFF", 0),

    ("invalid_payment_method", 1): ("SEND_UPDATE_LINK", 0),
    ("invalid_payment_method", 2): ("STOP_HANDOFF", 0),
    ("invalid_payment_method", 3): ("STOP_HANDOFF", 0),  # unreachable; keeps the table total

    # Defence in depth only: I4 stops an `unknown` case before the policy is consulted.
    ("unknown", 1): ("STOP_HANDOFF", 0),
    ("unknown", 2): ("STOP_HANDOFF", 0),
    ("unknown", 3): ("STOP_HANDOFF", 0),
}

# ------------------------------------------------------------ copy constraints
SYNTHETIC_DISCLOSURE = "[SYNTHETIC DEMO]"
LINK_PLACEHOLDER = "{LINK}"
COPY_MAX_CHARS = 320
COPY_FORBIDDEN = ("refund", "guarantee", "legal", "penalty", "last chance")

# Static templates exist for every (category, contact-action) pair, so the system is
# fully functional with the copy LLM switched off (LLM_COPY_ENABLED=false or
# LLM_PROVIDER=none). The only number any template may contain is the amount —
# the copy validator enforces that, including against these templates.
_LINK = LINK_PLACEHOLDER
_D = SYNTHETIC_DISCLOSURE
STATIC_TEMPLATES: dict[tuple[str, str], str] = {
    ("card_expired", "SEND_UPDATE_LINK"):
        f"{_D} Your subscription payment of Rs {{amount}} could not be completed because the card on "
        f"file has expired. Add a current card here: {_LINK}",
    ("card_expired", "PROMISE_TO_PAY"):
        f"{_D} Your subscription payment of Rs {{amount}} is still pending. Tell us when you can pay "
        f"and settle it here: {_LINK}",

    ("insufficient_funds", "SEND_UPDATE_LINK"):
        f"{_D} We could not collect Rs {{amount}} for your subscription. You can pay it directly "
        f"here: {_LINK}",
    ("insufficient_funds", "PROMISE_TO_PAY"):
        f"{_D} We have tried collecting Rs {{amount}} for your subscription without success. Pay "
        f"within three days to keep it active: {_LINK}",

    ("issuer_declined", "SEND_UPDATE_LINK"):
        f"{_D} Your bank declined the subscription charge of Rs {{amount}}. You can complete the "
        f"payment or use another method here: {_LINK}",
    ("issuer_declined", "PROMISE_TO_PAY"):
        f"{_D} The charge of Rs {{amount}} was declined by your bank. Settle it within three days "
        f"here: {_LINK}",

    ("authentication_failed", "SEND_UPDATE_LINK"):
        f"{_D} The authentication for your subscription payment of Rs {{amount}} did not complete. "
        f"Here is a fresh secure link: {_LINK}",
    ("authentication_failed", "PROMISE_TO_PAY"):
        f"{_D} Your subscription payment of Rs {{amount}} is still unauthenticated. Complete it "
        f"within three days: {_LINK}",

    ("invalid_payment_method", "SEND_UPDATE_LINK"):
        f"{_D} The payment method saved for your subscription is no longer usable, so Rs {{amount}} "
        f"could not be collected. Add a new one here: {_LINK}",
    ("invalid_payment_method", "PROMISE_TO_PAY"):
        f"{_D} We still could not collect Rs {{amount}} for your subscription. Add a working payment "
        f"method within three days: {_LINK}",

    ("unknown", "SEND_UPDATE_LINK"):
        f"{_D} A payment of Rs {{amount}} for your subscription did not go through. You can "
        f"complete it here: {_LINK}",
    ("unknown", "PROMISE_TO_PAY"):
        f"{_D} A payment of Rs {{amount}} for your subscription is outstanding. You can settle it "
        f"here: {_LINK}",
}

# Placeholder link used when an execution is simulated rather than a real test-mode
# Payment Link. `.invalid` is reserved by RFC 2606 and can never resolve.
SIMULATED_LINK_BASE = "https://example.invalid/pay/"
