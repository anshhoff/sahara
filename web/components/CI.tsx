/**
 * An estimate and its interval, rendered together and never apart.
 *
 * A point estimate without its interval is the easy dishonesty in this whole project,
 * so there is no component that can render one. `docs/analysis-plan.md` says a result
 * is claimed only when the interval excludes zero; an interval that spans zero is
 * labelled as spanning zero here rather than quietly rounded into a win.
 *
 * The interval is now also drawn. "95% CI [+31.0pp, +66.9pp]" is a sentence a reader
 * has to parse and then hold in their head while they parse the next one; a bar against
 * a marked zero line answers the only question anyone asks of it — does it cross zero,
 * and by how much — before the digits are read at all. The digits stay, because the plot
 * is the summary and the numbers are the record.
 */
import { Badge } from "./ui";

function Plot({
  value,
  ci,
  excludesZero,
}: {
  value: number;
  ci: [number, number];
  excludesZero: boolean;
}) {
  // A symmetric domain around zero, so the zero line always sits dead centre and
  // "does it cross?" is answered by position alone. The SCALE is per-plot, not shared:
  // one estimate is in rupees and the next in percentage points, so a common axis would
  // be meaningless. Two plots are therefore comparable in sign and in margin-relative-to-
  // its-own-interval, and NOT in width — which is why the numbers stay above the bar.
  // Padded by 15% so an interval endpoint never renders flush with the edge.
  const reach = Math.max(Math.abs(ci[0]), Math.abs(ci[1]), Math.abs(value)) * 1.15 || 1;
  const x = (v: number) => ((v + reach) / (2 * reach)) * 100;
  const lo = Math.min(x(ci[0]), x(ci[1]));
  const hi = Math.max(x(ci[0]), x(ci[1]));
  const tone = !excludesZero
    ? "var(--warn)"
    : value >= 0
      ? "var(--ok)"
      : "var(--danger)";

  return (
    <span aria-hidden="true" className="relative block h-[18px] w-full min-w-[120px]">
      {/* the axis */}
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-[var(--border)]" />
      {/* zero, marked and labelled by position rather than by colour */}
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-[var(--border-2)]" />
      {/* the interval */}
      <span
        className="absolute top-1/2 h-[7px] -translate-y-1/2 rounded-full opacity-30"
        style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 0.6)}%`, background: tone }}
      />
      {/* its ends */}
      {[lo, hi].map((p, i) => (
        <span
          key={i}
          className="absolute top-1/2 h-[11px] w-[1.5px] -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{ left: `${p}%`, background: tone }}
        />
      ))}
      {/* the point estimate */}
      <span
        className="absolute top-1/2 h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[var(--surface)]"
        style={{ left: `${x(value)}%`, background: tone }}
      />
    </span>
  );
}

export function Interval({
  value,
  ci,
  significant,
  format,
  /** Suppress the drawn plot where the row is too tight for it. */
  plot = true,
}: {
  value: number | null;
  ci: [number, number] | null;
  significant: boolean | null;
  format: (x: number) => string;
  plot?: boolean;
}) {
  if (value === null || ci === null) {
    return <span className="text-[var(--text-3)]">—</span>;
  }
  const excludesZero = significant !== false;
  return (
    <span className="inline-flex min-w-[150px] flex-col gap-[3px]">
      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-[2px]">
        <strong className="tnum text-[14px] tracking-tight">{format(value)}</strong>
        <span className="tnum text-[11.5px] text-[var(--text-2)]">
          95% CI [{format(ci[0])}, {format(ci[1])}]
        </span>
        {significant === false && <Badge tone="warn">spans zero</Badge>}
      </span>
      {plot && <Plot value={value} ci={ci} excludesZero={excludesZero} />}
    </span>
  );
}
