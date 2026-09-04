/**
 * The live-demo client.
 *
 * These four routes are the only ones in the console that WRITE, and they are the only
 * ones the typed `get()` client cannot cover: every one of them takes its arguments as
 * query parameters on a POST (see `app/live_demo.py`), which `openapi-typescript` models
 * but `GetPath` — by construction a map of GET paths — does not reach. Saying that here
 * is more useful than quietly widening the typed client to admit writes it was built to
 * exclude.
 *
 * The demo is addressed two ways and both are real:
 *   demo_id — a checkout run, which may open several successive cases
 *   case_id — one specific case, which is how the inject shortcut and the console link in
 *
 * `_resolve_case_id` on the server accepts either, so the whole client passes a
 * discriminated `Ref` around rather than two optional strings that can both be unset.
 */
import { API_BASE } from "./api";
import type { AuditEntry, CaseRecord } from "./types";

export type Ref = { demo_id: string } | { case_id: string };

export const refQuery = (ref: Ref) =>
  "demo_id" in ref
    ? `demo_id=${encodeURIComponent(ref.demo_id)}`
    : `case_id=${encodeURIComponent(ref.case_id)}`;

export type DemoConfig = {
  configured: boolean;
  key_id: string | null;
  amount_paise: number;
};

export type DemoOrder = {
  demo_id: string;
  order_id: string;
  key_id: string;
  amount: number;
  currency: string;
};

/** What the executor actually did, and — when it fell back — the reason it recorded. */
export type Execution = {
  mode?: string | null;
  result_payload?: {
    link_url?: string | null;
    /** Which Razorpay surface produced `link_url` — see `_update_link_surface`. */
    link_surface?: "payment_link" | "checkout_order" | "none" | null;
    razorpay_response?: { skipped?: string; error?: string } | null;
  } | null;
};

export type Decision = {
  id: string;
  action: string;
  status: string;
  scheduled_for?: string | null;
  execution?: Execution | null;
};

/** `/api/demo/case` hands back exactly what `/api/cases/{id}` does. */
export type DemoCase = {
  case: CaseRecord & { amount_rupees?: number };
  decisions: Decision[];
  audit_trail: AuditEntry[];
  bounds?: Record<string, number>;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers: { accept: "application/json" },
    ...init,
  });
  if (!res.ok) {
    // FastAPI puts the operator-facing reason in `detail`; surfacing the raw status
    // instead would throw away the only part of the response worth reading.
    const body = (await res.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export const demoConfig = () => call<DemoConfig>("/api/demo/config");

export const createOrder = () => call<DemoOrder>("/api/demo/order", { method: "POST" });

export const readCase = (ref: Ref) => call<DemoCase>(`/api/demo/case?${refQuery(ref)}`);

export const forceTick = (ref: Ref) =>
  call<{ pulled_forward: number }>(`/api/demo/force-tick?${refQuery(ref)}`, { method: "POST" });

export const simulate = (ref: Ref, outcome: "recovered" | "failed_again") =>
  call<DemoCase>(`/api/demo/simulate?outcome=${outcome}&${refQuery(ref)}`, { method: "POST" });

export const injectCategory = (category: string, amountRupees: number) =>
  call<{ case_id: string | null; status: string; requested_category: string }>(
    "/api/control/inject",
    {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ category, amount_rupees: amountRupees }),
    },
  );

/**
 * The link the executor produced, and whether it is payable or a fail-closed stand-in.
 *
 * `real` and `surface` answer different questions and the page shows both. `real` is
 * "could a customer pay this?"; `surface` is which Razorpay endpoint served it, which
 * matters because a hosted Payment Link and an Order opened in the console's own /pay
 * page look nothing alike to a viewer even though both are live test-mode calls.
 *
 * A voice call reuses whatever link the case was already sent rather than minting a
 * second one, so the same URL can appear on more than one decision. The first hit
 * wins, which is the one the customer would have acted on.
 */
export function linkFrom(decisions: Decision[]): {
  url: string;
  real: boolean;
  surface: "payment_link" | "checkout_order" | "none";
  reason?: string;
} | null {
  for (const d of decisions) {
    const url = d.execution?.result_payload?.link_url;
    if (!url) continue;
    const real = d.execution?.mode === "razorpay_test";
    const r = d.execution?.result_payload?.razorpay_response ?? {};
    return {
      url,
      real,
      surface: d.execution?.result_payload?.link_surface ?? (real ? "payment_link" : "none"),
      // Never invent a reason the executor did not record.
      reason: real ? undefined : (r.skipped ?? r.error ?? "no reason recorded by the executor"),
    };
  }
  return null;
}

// ------------------------------------------------------------------ scripted run
/** One narrated step of a scripted run. `skipped_hours` is real time stood in for. */
export type RunBeat = {
  n: number;
  label: string;
  detail: string;
  elapsed_seconds: number;
  skipped_hours: number;
  demo_now: string;
};

export type DemoRun = {
  run_id?: string;
  case_id?: string;
  category?: string;
  status: "idle" | "running" | "finished" | "error";
  error?: string | null;
  elapsed_seconds?: number;
  beats: RunBeat[];
  /** Only on the POST that starts a run: where to watch it. */
  watch?: string;
};

export const startRun = (category: string, amountRupees: number, finalOutcome: string) =>
  call<DemoRun>(
    `/api/demo/run?category=${encodeURIComponent(category)}` +
      `&amount_rupees=${amountRupees}&final_outcome=${encodeURIComponent(finalOutcome)}`,
    { method: "POST" },
  );

export const runStatus = () => call<DemoRun>("/api/demo/run");

export type OrderInfo = {
  order_id: string;
  amount: number;
  currency: string;
  status: string;
  key_id: string;
  case_id: string | null;
};

export const orderInfo = (orderId: string) =>
  call<OrderInfo>(`/api/demo/order-info?order_id=${encodeURIComponent(orderId)}`);

export const latestScheduled = (decisions: Decision[]): Decision | null =>
  [...decisions].reverse().find((d) => d.status === "scheduled") ?? null;

/** Razorpay Checkout, loaded from their CDN — typed here rather than `any` at each use. */
export type RazorpayOptions = {
  key: string;
  amount: number;
  currency: string;
  order_id: string;
  name: string;
  description?: string;
  notes?: Record<string, string>;
  handler?: (response: unknown) => void;
  modal?: { ondismiss?: () => void };
};

export type RazorpayInstance = {
  open: () => void;
  on: (event: string, cb: (payload: unknown) => void) => void;
};

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayInstance;
  }
}
