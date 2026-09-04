/**
 * Formatting, in one place.
 *
 * Money lives in PAISE end to end — in SQLite, in the API, and here — and is converted
 * only at the moment it is printed. Floats and money are a bad combination and the
 * conversion belongs at the edge, not scattered through the code that adds things up.
 */

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const INR2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const rupees = (paise: number | null | undefined) =>
  "₹" + INR.format(Math.round((paise ?? 0) / 100));

export const rupees2 = (paise: number | null | undefined) =>
  "₹" + INR2.format((paise ?? 0) / 100);

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "—" : (100 * x).toFixed(digits) + "%";

/** Percentage points, always signed — a lift of +0.0pp and a lift of −0.0pp are
 *  different readings and the sign is the interesting half. */
export const pp = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "—" : (x >= 0 ? "+" : "") + (100 * x).toFixed(digits) + "pp";

export const words = (s: string | null | undefined) => String(s ?? "").replace(/_/g, " ");

export const hours = (h: number | null | undefined) =>
  h === null || h === undefined ? "—" : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} d`;

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z").replace(/Z$/, " UTC");
}

/** Which arm a case belongs to. The control arm has its own colour everywhere in
 *  this console, which is the one place colour does carry meaning — and it always
 *  ships with the word "control" next to it. */
export const arm = (c: { is_holdout: number }) => (c.is_holdout ? "control" : "treated");

export const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | "control" | "neutral"> = {
  recovered: "ok",
  open: "neutral",
  stopped_holdout: "control",
  stopped_handoff: "warn",
  stopped_max_attempts: "warn",
  stopped_cooldown_expired: "warn",
  stopped_opt_out: "danger",
  stopped_suppressed: "danger",
  stopped_unknown: "neutral",
  stopped_uneconomic: "neutral",
  stopped_already_settled: "ok",
  stopped_unverified_recipient: "danger",
};
