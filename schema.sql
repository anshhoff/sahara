-- Failed Subscription Recovery Agent — full schema (docs/03-data-model.md).
-- Applied at startup by app/db.py. No migrations framework by design (docs/10 §9).
--
-- The CHECK constraints on `category` and `action` are not decoration: they are the
-- schema-level backstop of the LLM boundary (docs/03 §8). Even a buggy caller cannot
-- insert a category or an action outside the fixed enums.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- RecoveryCase
CREATE TABLE IF NOT EXISTS recovery_case (
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  customer_opted_out INTEGER NOT NULL DEFAULT 0 CHECK (customer_opted_out IN (0,1)),
  amount_at_risk_paise INTEGER NOT NULL CHECK (amount_at_risk_paise >= 0),
  currency TEXT NOT NULL DEFAULT 'INR',
  status TEXT NOT NULL CHECK (status IN ('open','recovered','stopped_max_attempts',
    'stopped_cooldown_expired','stopped_opt_out','stopped_unknown','stopped_handoff')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
  last_contact_at TEXT,
  current_category TEXT CHECK (current_category IN ('card_expired','insufficient_funds',
    'issuer_declined','authentication_failed','invalid_payment_method','unknown')
    OR current_category IS NULL),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ---------------------------------------------------------------- FailureEvent
-- Immutable after insert. `razorpay_event_id` UNIQUE is what makes intake idempotent
-- against Razorpay's at-least-once webhook delivery (docs/02 §6.6).
CREATE TABLE IF NOT EXISTS failure_event (
  id TEXT PRIMARY KEY,
  case_id TEXT REFERENCES recovery_case(id),
  source TEXT NOT NULL CHECK (source IN ('webhook','synthetic')),
  razorpay_event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL CHECK (event_type IN ('payment.failed','subscription.pending',
    'subscription.halted','subscription.charged','payment_link.paid')),
  subscription_id TEXT NOT NULL,
  payment_id TEXT,
  customer_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
  currency TEXT NOT NULL DEFAULT 'INR',
  error_code TEXT,
  error_description TEXT,
  error_source TEXT,
  error_step TEXT,
  error_reason TEXT,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  raw_payload TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ------------------------------------------------------------- DiagnosisResult
CREATE TABLE IF NOT EXISTS diagnosis_result (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES recovery_case(id),
  failure_event_id TEXT NOT NULL REFERENCES failure_event(id),
  category TEXT NOT NULL CHECK (category IN ('card_expired','insufficient_funds',
    'issuer_declined','authentication_failed','invalid_payment_method','unknown')),
  method TEXT NOT NULL CHECK (method IN ('rule','llm')),
  matched_rule TEXT,
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
  llm_model TEXT,
  llm_raw_response TEXT,
  created_at TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- --------------------------------------------------------- InterventionDecision
CREATE TABLE IF NOT EXISTS intervention_decision (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES recovery_case(id),
  attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 3),
  category TEXT NOT NULL CHECK (category IN ('card_expired','insufficient_funds',
    'issuer_declined','authentication_failed','invalid_payment_method','unknown')),
  action TEXT NOT NULL CHECK (action IN ('RETRY_LATER','SEND_UPDATE_LINK',
    'PROMISE_TO_PAY','STOP_HANDOFF')),
  policy_row_ref TEXT NOT NULL,
  invariant_check TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  scheduled_for TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('scheduled','executed','blocked_by_invariant','superseded')),
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ------------------------------------------------------------- ExecutionRecord
CREATE TABLE IF NOT EXISTS execution_record (
  id TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL UNIQUE REFERENCES intervention_decision(id),
  case_id TEXT NOT NULL REFERENCES recovery_case(id),
  action TEXT NOT NULL CHECK (action IN ('RETRY_LATER','SEND_UPDATE_LINK',
    'PROMISE_TO_PAY','STOP_HANDOFF')),
  mode TEXT NOT NULL CHECK (mode IN ('razorpay_test','simulated')),
  razorpay_ref TEXT,
  simulated_channel TEXT CHECK (simulated_channel IN ('sms','email') OR simulated_channel IS NULL),
  message_copy TEXT,
  copy_source TEXT CHECK (copy_source IN ('llm_draft','static_template') OR copy_source IS NULL),
  copy_validation TEXT,
  status TEXT NOT NULL CHECK (status IN ('success','failed')),
  result_payload TEXT,
  executed_at TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ---------------------------------------------------------------- AuditLogEntry
-- APPEND ONLY. There is no UPDATE or DELETE against this table anywhere in app/;
-- tests/test_audit_append_only.py greps for one and fails the build if it appears.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL REFERENCES recovery_case(id),
  seq INTEGER NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('detect','diagnose','decide','execute','stop','outcome')),
  actor TEXT NOT NULL CHECK (actor IN ('system','llm','razorpay','human')),
  summary TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ------------------------------------------------------------------- run metadata
-- One row per batch run, so the dashboard banner can state the seed honestly (docs/06 §1.2).
CREATE TABLE IF NOT EXISTS batch_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seed INTEGER,
  n_cases INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_case_status ON recovery_case(status);
CREATE INDEX IF NOT EXISTS idx_case_subscription ON recovery_case(subscription_id, status);
CREATE INDEX IF NOT EXISTS idx_event_case ON failure_event(case_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_case ON diagnosis_result(case_id);
CREATE INDEX IF NOT EXISTS idx_decision_case ON intervention_decision(case_id);
CREATE INDEX IF NOT EXISTS idx_decision_due ON intervention_decision(status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_execution_case ON execution_record(case_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_case_seq ON audit_log(case_id, seq);
