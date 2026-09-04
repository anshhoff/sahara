/**
 * An estimate and its interval, rendered together and never apart.
 *
 * A point estimate without its interval is the easy dishonesty in this whole project,
 * so there is no component that can render one. `docs/analysis-plan.md` says a result
 * is claimed only when the interval excludes zero; an interval that spans zero is
 * labelled as spanning zero here rather than quietly rounded into a win.
 */
import { Badge } from "./ui";

export function Interval({
  value,
  ci,
  significant,
  format,
}: {
  value: number | null;
  ci: [number, number] | null;
  significant: boolean | null;
  format: (x: number) => string;
}) {
  if (value === null || ci === null) {
    return <span className="text-[var(--text-3)]">—</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-baseline gap-2">
      <strong className="tnum">{format(value)}</strong>
      <span className="tnum text-[12px] text-[var(--text-2)]">
        95% CI [{format(ci[0])}, {format(ci[1])}]
      </span>
      {significant === false && <Badge tone="warn">spans zero</Badge>}
    </span>
  );
}
