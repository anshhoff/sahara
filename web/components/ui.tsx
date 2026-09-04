/**
 * The primitives every page is built from.
 *
 * Server components with no state, so they render on the server like the pages that
 * use them — the read paths in this console have nothing to be interactive about, and
 * shipping them as client components would send JavaScript to draw a table.
 */
import Link from "next/link";
import type { ReactNode } from "react";

type Tone = "ok" | "warn" | "danger" | "control" | "neutral" | "accent";

const TONE: Record<Tone, string> = {
  ok: "bg-[var(--ok-soft)] text-[var(--ok)]",
  warn: "bg-[var(--warn-soft)] text-[var(--warn)]",
  danger: "bg-[var(--danger-soft)] text-[var(--danger)]",
  control: "bg-[var(--control-soft)] text-[var(--control)]",
  neutral: "bg-[var(--neutral-soft)] text-[var(--neutral)]",
  accent: "bg-[var(--accent-soft)] text-[var(--accent)]",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-[var(--r-sm)] px-2 py-[2px] text-[12px] font-medium whitespace-nowrap ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

export function Panel({
  title,
  aside,
  lede,
  children,
}: {
  title?: string;
  aside?: ReactNode;
  lede?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[var(--r)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-1)]">
      {(title || aside) && (
        <header className="flex flex-wrap items-baseline gap-3 border-b border-[var(--border)] px-4 py-3">
          {title && <h2 className="text-[14px] font-semibold tracking-tight">{title}</h2>}
          <span className="grow" />
          {aside && <span className="text-[12px] text-[var(--text-3)]">{aside}</span>}
        </header>
      )}
      <div className="px-4 py-4">
        {lede && <p className="mb-4 max-w-[72ch] text-[13px] leading-relaxed text-[var(--text-2)]">{lede}</p>}
        {children}
      </div>
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="rounded-[var(--r)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3 shadow-[var(--shadow-1)]">
      <div className="text-[12px] font-medium text-[var(--text-3)]">{label}</div>
      <div
        className={`tnum mt-1 text-[24px] leading-tight font-semibold ${
          tone === "control" ? "text-[var(--control)]" : tone === "ok" ? "text-[var(--ok)]" : ""
        }`}
      >
        {value}
      </div>
      {sub && <div className="mt-1 text-[12px] leading-snug text-[var(--text-2)]">{sub}</div>}
    </div>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-[var(--border-2)] text-left">
            {head.map((h, i) => (
              <th
                key={i}
                className="px-2 py-2 text-[12px] font-semibold whitespace-nowrap text-[var(--text-3)]"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({ children }: { children: ReactNode }) {
  return <tr className="border-b border-[var(--border)] last:border-0">{children}</tr>;
}

export function Cell({
  children,
  num,
  className = "",
}: {
  children: ReactNode;
  num?: boolean;
  className?: string;
}) {
  return (
    <td className={`px-2 py-2 align-top ${num ? "tnum text-right" : ""} ${className}`}>{children}</td>
  );
}

/** The one component that exists because of a rule rather than a layout: a number the
 *  backend could not measure must never render as 0. "No effect" and "no measurement"
 *  are the distinction this whole project turns on. */
export function Unmeasured({ why }: { why?: string }) {
  return (
    <span className="text-[var(--text-3)]" title={why}>
      — <span className="text-[11px]">not measurable</span>
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[var(--r)] border border-dashed border-[var(--border-2)] bg-[var(--surface-2)] px-4 py-8 text-center text-[13px] text-[var(--text-2)]">
      {children}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 max-w-[80ch] border-t border-[var(--border)] pt-3 text-[12px] leading-relaxed text-[var(--text-3)]">
      {children}
    </p>
  );
}

export function CaseLink({ id }: { id: string }) {
  return (
    <Link href={`/cases/${id}`} className="mono text-[var(--accent)] hover:underline">
      {id}
    </Link>
  );
}
