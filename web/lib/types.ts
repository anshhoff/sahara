/**
 * Domain shapes for the JSON the API returns.
 *
 * Hand-written, and honestly so: every dashboard route is annotated `-> dict[str, Any]`
 * in Python, so the generated OpenAPI describes each body as an open object and cannot
 * prove these. They are asserted against `app/metrics.py`, and `api.ts` explains why
 * that is the boundary of the guarantee.
 *
 * Every field that can be genuinely unmeasurable is `| null` rather than defaulted to
 * zero, because the difference between "no effect" and "no measurement" is the single
 * distinction this project cares most about. A `0` where the backend meant `null` would
 * quietly turn missing evidence into evidence of absence.
 */

export type Arm = {
  n: number;
  recovered: number;
  rate: number;
  organic?: number | null;
  organic_rate?: number | null;
  untouched?: number;
};

export type Incremental = {
  available: boolean;
  reason?: string;
  treated: Arm;
  control: Arm;
  lift: number;
  lift_ci95: [number, number];
  significant: boolean;
  incremental_paise_total: number;
  incremental_paise_ci95: [number, number];
  gross_recovered_paise: number;
  organic_balance?: {
    available: boolean;
    treated: number | null;
    control: number | null;
    note: string;
  };
};

export type CategoryLift = {
  category: string;
  treated: Arm;
  control: Arm;
  lift: number | null;
  lift_ci95: [number, number] | null;
  significant: boolean | null;
  reason?: string;
};

export type CalibrationPair = {
  category: string;
  action: string;
  n: number;
  prior: number;
  realised: number;
  gap: number;
  direction: "optimistic" | "pessimistic" | "exact";
};

export type Calibration = {
  available: boolean;
  reason?: string;
  n_scored: number;
  n_executions_total: number;
  brier_score: number;
  ece: number;
  reliability: {
    bin: string;
    n: number;
    mean_prior: number | null;
    realised: number | null;
    gap: number | null;
  }[];
  by_pair: CalibrationPair[];
  attribution: string;
};

export type Fencing = {
  n_dispatches_fenced: number;
  outreach_to_settled: number;
  outreach_to_settled_case_ids: string[];
  stopped_already_settled: number;
  by_phase: Record<string, Record<string, number>>;
  n_compensations: number;
  compensations: { case_id: string; seq: number; summary: string; created_at: string }[];
  claim: string;
  note: string;
};

export type Promises = {
  n_promises: number;
  promises_kept: number;
  promises_broken: number;
  promises_open: number;
  kept_rate: number | null;
  case_ids: Record<string, string[]>;
  by_reading_source: Record<string, Record<string, number>>;
  note: string;
};

export type Declined = {
  n_declined: number;
  n_control_arm: number;
  by_reason: { status: string; n: number; why: string }[];
  case_ids: string[];
  note: string;
};

export type Latency = {
  by_stage: Record<
    string,
    { n: number; p50_ms: number; p95_ms: number; max_ms: number; mean_ms: number }
  >;
  basis: string;
};

export type Summary = {
  n_cases: number;
  n_synthetic: number;
  n_live: number;
  seed: number | null;
  total_at_risk_paise: number;
  total_recovered_paise: number;
  recovery_rate: {
    numerator: number;
    denominator: number;
    rate: number;
    strict_denominator: number;
    strict_rate: number;
    n_open: number;
  };
  avg_time_to_recovery_hours: number | null;
  time_basis: string;
  n_open: number;
  n_recovered: number;
  stopped: { total: number; by_status: Record<string, number> };
  llm: Record<string, number>;
  execution_modes: Record<string, number>;
  executions_by_action: Record<string, number>;
  promises: Promises;
  reconciliation: Record<string, unknown>;
  incremental: Incremental;
  lift_by_category: CategoryLift[];
  calibration: Calibration;
  declined_to_contact: Declined;
  latency: Latency;
  fencing: Fencing;
  costs: {
    outreach_paise: number;
    n_handoff_cases: number;
    handoff_paise: number;
    total_paise: number;
    by_action: Record<string, { n: number; paise: number }>;
    unit_costs_paise: Record<string, number>;
  };
  net: {
    gross_recovered_paise: number;
    total_cost_paise: number;
    net_recovered_paise: number;
    cost_per_100_recovered: number | null;
    incremental_available: boolean;
    incremental_paise?: number;
    net_incremental_paise?: number;
    net_incremental_paise_ci95?: [number, number];
  };
  audit_chain: {
    status: string;
    n_entries: number;
    n_checked: number;
    n_unchained: number;
    head: string | null;
    first_break: unknown;
  };
  bounds: Record<string, unknown>;
};

/**
 * A row from `GET /api/cases`.
 *
 * The list endpoint aliases its columns — `id AS case_id`, `current_category AS
 * category` — and the detail endpoint does not. These are genuinely two shapes and
 * they are typed as two shapes; collapsing them behind one optimistic type is how a
 * frontend ends up rendering `undefined` in a table cell, which is exactly what
 * happened before this was split.
 */
export type CaseRow = {
  case_id: string;
  subscription_id: string;
  status: string;
  category: string | null;
  attempt_count: number;
  amount_at_risk_paise: number;
  amount_rupees: number;
  is_holdout: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  customer_opted_out: number;
  synthetic: number;
};

/** A case as `GET /api/cases/{case_id}` returns it, under `case`. */
export type CaseRecord = {
  id: string;
  subscription_id: string;
  customer_id: string;
  status: string;
  current_category: string | null;
  attempt_count: number;
  amount_at_risk_paise: number;
  amount_rupees: number;
  is_holdout: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  last_contact_at: string | null;
  customer_opted_out: number;
  synthetic: number;
};

export type AuditEntry = {
  id: number;
  seq: number;
  stage: string;
  actor: string;
  summary: string;
  detail: Record<string, unknown>;
  created_at: string;
  entry_hash: string | null;
  prev_hash: string | null;
};

export type CaseDetail = {
  case: CaseRecord;
  events?: Record<string, unknown>[];
  diagnoses?: Record<string, unknown>[];
  decisions?: Record<string, unknown>[];
  executions?: Record<string, unknown>[];
  audit?: AuditEntry[];
  [key: string]: unknown;
};

export type Mechanism = {
  invariants: {
    code: string;
    title: string;
    rule: string;
    plain: string;
    kind: string;
    stops: number;
    defers: number;
  }[];
  policy: { category: string; attempt: number; action: string; delay_hours: number }[];
  rules: { id: string; category: string; n_matched: number; patterns: string[]; n_patterns: number }[];
  categories: string[];
  actions: string[];
  counts: Record<string, number>;
  bounds: Record<string, number>;
  copy: Record<string, unknown>;
};
