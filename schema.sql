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
    'stopped_cooldown_expired','stopped_opt_out','stopped_unknown','stopped_handoff',
    'stopped_uneconomic','stopped_suppressed','stopped_holdout')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
  last_contact_at TEXT,
  current_category TEXT CHECK (current_category IN ('card_expired','insufficient_funds',
    'issuer_declined','authentication_failed','invalid_payment_method','unknown')
    OR current_category IS NULL),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1)),
  -- Randomised control arm. A holdout case is detected and diagnosed exactly like any
  -- other, then stopped before a single intervention, so recovery can be reported as
  -- INCREMENTAL (treated minus control) rather than gross.
  is_holdout INTEGER NOT NULL DEFAULT 0 CHECK (is_holdout IN (0,1))
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
  -- The economics of this decision, computed BEFORE it was taken (app/economics.py).
  -- Written on every decision, not only the ones the gate stopped, so that a reader
  -- can see what the agent expected to gain from an action it went ahead with.
  ev_paise INTEGER,
  ev_detail TEXT,
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
  -- What this execution cost to perform, in paise. Summed into the net-recovery
  -- metrics, so money recovered is never reported without the money it took.
  cost_paise INTEGER NOT NULL DEFAULT 0 CHECK (cost_paise >= 0),
  executed_at TEXT NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ---------------------------------------------------------------- AuditLogEntry
-- APPEND ONLY. There is no UPDATE or DELETE against this table anywhere in app/;
-- tests/test_llm_boundary.py greps for one and fails the build if it appears.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL REFERENCES recovery_case(id),
  seq INTEGER NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('detect','diagnose','decide','execute','stop','outcome')),
  actor TEXT NOT NULL CHECK (actor IN ('system','llm','razorpay','human')),
  summary TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL,
  -- Tamper evidence. entry_hash = sha256 over this row's canonical JSON together
  -- with the previous entry's hash, across the WHOLE log rather than per case, so
  -- deleting an entire case's trail breaks the chain just as loudly as editing one
  -- word of it. Nullable only so that a database written before this column existed
  -- still opens; app/audit.py fills both on every write and verify() reports any
  -- unchained rows separately rather than passing them silently.
  prev_hash TEXT,
  entry_hash TEXT,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0,1))
);

-- ------------------------------------------------------------------ suppression
-- An opt-out recorded against the PERSON, not one of their cases (invariant I6).
-- recovery_case.customer_opted_out is per-case and is what I3 reads; this table is
-- what makes an opt-out on one subscription stop the agent on that customer's other
-- subscriptions too, including ones whose cases do not exist yet.
CREATE TABLE IF NOT EXISTS suppression (
  customer_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL CHECK (reason IN ('opt_out','complaint','manual')),
  source TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_execution_executed_at ON execution_record(executed_at);
CREATE INDEX IF NOT EXISTS idx_case_customer ON recovery_case(customer_id);
