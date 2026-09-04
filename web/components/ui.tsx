/**
 * The primitives every page is built from.
 *
 * Server components with no state, so they render on the server like the pages that
 * use them — the read paths in this console have nothing to be interactive about, and
 * shipping them as client components would send JavaScript to draw a table.
 *
 * The rewrite here is a hierarchy rewrite, not a colour one. The palette was already
 * considered; what was missing was any way for one number to matter more than another.
 * Every panel, stat and row previously rendered at the same weight, so a reader landing
 * on the overview had nine equally loud things and no idea which one the project stands
 * behind. `Stat` now has a hero size, `Panel` has a lede that reads as prose rather than
 * as more UI, and tables have a head that is visibly a head.
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { IconChevronRight, IconEmpty } from "./icons";

export type Tone = "ok" | "warn" | "danger" | "control" | "neutral" | "accent";

const TONE: Record<Tone, { chip: string; text: string; dot: string; line: string; soft: string }> = {
  ok: {
    chip: "bg-[var(--ok-soft)] text-[var(--ok)] border-[var(--ok-line)]",
    text: "text-[var(--ok)]",
    dot: "bg-[var(--ok)]",
    line: "border-[var(--ok-line)]",
    soft: "bg-[var(--ok-soft)]",
  },
  warn: {
    chip: "bg-[var(--warn-soft)] text-[var(--warn)] border-[var(--warn-line)]",
    text: "text-[var(--warn)]",
    dot: "bg-[var(--warn)]",
    line: "border-[var(--warn-line)]",
    soft: "bg-[var(--warn-soft)]",
  },
  danger: {
    chip: "bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger-line)]",
    text: "text-[var(--danger)]",
    dot: "bg-[var(--danger)]",
    line: "border-[var(--danger-line)]",
    soft: "bg-[var(--danger-soft)]",
  },
  control: {
    chip: "bg-[var(--control-soft)] text-[var(--control)] border-[var(--control-line)]",
    text: "text-[var(--control)]",
    dot: "bg-[var(--control)]",
    line: "border-[var(--control-line)]",
    soft: "bg-[var(--control-soft)]",
  },
  neutral: {
    chip: "bg-[var(--neutral-soft)] text-[var(--neutral)] border-[var(--neutral-line)]",
    text: "text-[var(--neutral)]",
    dot: "bg-[var(--neutral)]",
    line: "border-[var(--neutral-line)]",
    soft: "bg-[var(--neutral-soft)]",
  },
  accent: {
    chip: "bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent-line)]",
    text: "text-[var(--accent)]",
    dot: "bg-[var(--accent)]",
    line: "border-[var(--accent-line)]",
    soft: "bg-[var(--accent-soft)]",
  },
};

/**
 * A state chip.
 *
 * The dot is not decoration. Status here is the one thing a reader scans for down a
 * column of ninety rows, and a chip distinguished only by fill is a chip that stops
 * working in greyscale, in a screenshot pasted into a deck, and for the eight percent
 * of men reading it. Shape plus label plus colour; colour last.
 */
export function Badge({
  tone = "neutral",
  dot = true,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  const t = TONE[tone];
  return (
    <span
      className={`inline-flex items-center gap-[5px] rounded-full border px-[7px] py-[1px] text-[12px] leading-[17px] font-medium whitespace-nowrap ${t.chip}`}
    >
      {dot && <span aria-hidden="true" className={`h-[5px] w-[5px] shrink-0 rounded-full ${t.dot}`} />}
      {children}
    </span>
  );
}

/**
 * A titled surface.
 *
 * `flush` exists because a table inside a padded box wastes 32px of horizontal room on
 * every page and then makes the last column scroll — a table wants the full width of its
 * panel and its own internal padding, not the panel's.
 */
export function Panel({
  title,
  aside,
  lede,
  tone,
  flush,
  footer,
  children,
}: {
  title?: string;
  aside?: ReactNode;
  lede?: ReactNode;
  tone?: Tone;
  flush?: boolean;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-1)]">
      {(title || aside) && (
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--border)] bg-[var(--surface-2)] px-4 py-[10px]">
          {tone && (
            <span aria-hidden="true" className={`h-[6px] w-[6px] shrink-0 rounded-full ${TONE[tone].dot}`} />
          )}
          {title && <h2 className="text-[13.5px] font-semibold tracking-tight">{title}</h2>}
          <span className="grow" />
          {aside && (
            <span className="tnum text-[11.5px] font-medium text-[var(--text-3)]">{aside}</span>
          )}
        </header>
      )}
      {lede && (
        <div className="border-b border-[var(--border)] px-4 py-3">
          <p className="max-w-[78ch] text-[13px] leading-[1.65] text-[var(--text-2)]">{lede}</p>
        </div>
      )}
      <div className={flush ? "" : "px-4 py-4"}>{children}</div>
      {footer && (
        <div className="border-t border-[var(--border)] bg-[var(--surface-2)] px-4 py-[10px]">{footer}</div>
      )}
    </section>
  );
}

/**
 * A single figure.
 *
 * Three sizes, because three is how many levels of importance this console actually
 * has: the one number the project stands behind, the numbers that qualify it, and the
 * numbers that describe the batch. Previously all of them rendered at 24px.
 */
export function Stat({
  label,
  value,
  sub,
  tone,
  size = "md",
  href,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  size?: "sm" | "md" | "hero";
  href?: string;
}) {
  const valueSize =
    size === "hero"
      ? "text-[clamp(30px,5vw,var(--t-4xl))] leading-[1.05] tracking-[-0.02em]"
      : size === "sm"
        ? "text-[var(--t-lg)] leading-tight"
        : "text-[var(--t-2xl)] leading-[1.15] tracking-[-0.015em]";

  const body = (
    <>
      <div className="flex items-center gap-[6px]">
        {tone && (
          <span aria-hidden="true" className={`h-[6px] w-[6px] shrink-0 rounded-full ${TONE[tone].dot}`} />
        )}
        <span className="eyebrow">{label}</span>
      </div>
      <div className={`tnum mt-[6px] font-semibold ${valueSize} ${tone ? TONE[tone].text : ""}`}>
        {value}
      </div>
      {sub && (
        <div
          className={`mt-[6px] leading-snug text-[var(--text-2)] ${size === "hero" ? "max-w-[52ch] text-[12.5px]" : "text-[12px]"}`}
        >
          {sub}
        </div>
      )}
    </>
  );

  const shell = `relative overflow-hidden rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-1)] ${
    size === "hero" ? "px-5 py-5" : "px-4 py-[14px]"
  }`;

  if (href) {
    return (
      <Link
        href={href}
        className={`${shell} group block transition-shadow duration-[var(--dur)] hover:border-[var(--border-2)] hover:shadow-[var(--shadow-2)]`}
      >
        {body}
        <IconChevronRight
          size={14}
          className="absolute top-4 right-3 text-[var(--text-3)] opacity-0 transition-opacity duration-[var(--dur)] group-hover:opacity-100"
        />
      </Link>
    );
  }
  return <div className={shell}>{body}</div>;
}

/** A head cell may declare that its column is numeric, so the label sits over the
 *  digits instead of drifting to the far side of them. */
export type Head = ReactNode | { label: ReactNode; num?: boolean; w?: string };

function headParts(h: Head): { label: ReactNode; num: boolean; w?: string } {
  if (h && typeof h === "object" && !Array.isArray(h) && "label" in (h as object)) {
    const o = h as { label: ReactNode; num?: boolean; w?: string };
    return { label: o.label, num: !!o.num, w: o.w };
  }
  return { label: h as ReactNode, num: false };
}

/**
 * A data table.
 *
 * The head is sticky, because the longest table here is ninety cases and a column of
 * unlabelled numbers is not data. Rows highlight on hover, which is the cheapest way to
 * keep your eye on one row while it crosses seven columns.
 */
export function Table({ head, children }: { head: Head[]; children: ReactNode }) {
  return (
    <div className="scroll-x max-h-[min(72vh,880px)] overflow-y-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead className="sticky top-0 z-10">
          <tr className="bg-[var(--surface-2)] text-left">
            {head.map((h, i) => {
              const { label, num, w } = headParts(h);
              return (
                <th
                  key={i}
                  scope="col"
                  style={w ? { width: w } : undefined}
                  className={`border-b border-[var(--border-2)] px-3 py-[9px] text-[11px] font-semibold tracking-[.04em] whitespace-nowrap text-[var(--text-3)] uppercase ${
                    num ? "text-right" : ""
                  }`}
                >
                  {label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({ children, tone }: { children: ReactNode; tone?: Tone }) {
  return (
    <tr
      className={`border-b border-[var(--border)] transition-colors duration-[var(--dur-fast)] last:border-0 hover:bg-[var(--surface-2)] ${
        tone ? TONE[tone].soft : ""
      }`}
    >
      {children}
    </tr>
  );
}

/** A total or summary row: same table, visibly not one of the data rows. */
export function TotalRow({ children }: { children: ReactNode }) {
  return (
    <tr className="border-t-2 border-[var(--border-2)] bg-[var(--surface-2)] font-semibold">
      {children}
    </tr>
  );
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
    <td className={`px-3 py-[9px] align-top ${num ? "tnum text-right whitespace-nowrap" : ""} ${className}`}>
      {children}
    </td>
  );
}

/** The one component that exists because of a rule rather than a layout: a number the
 *  backend could not measure must never render as 0. "No effect" and "no measurement"
 *  are the distinction this whole project turns on. */
export function Unmeasured({ why }: { why?: string }) {
  return (
    <span
      className="inline-flex items-center gap-[5px] text-[var(--text-3)] italic"
      title={why}
    >
      <span aria-hidden="true" className="text-[var(--border-2)]">—</span>
      <span className="text-[11.5px] not-italic">not measurable</span>
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-[var(--r)] border border-dashed border-[var(--border-2)] bg-[var(--surface-2)] px-4 py-9 text-center text-[13px] leading-relaxed text-[var(--text-2)]">
      <IconEmpty size={22} className="mb-2 text-[var(--text-3)]" />
      <div className="max-w-[58ch]">{children}</div>
    </div>
  );
}

/** The caveat under a panel. Quieter than the panel, and never quieter than legible. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="mt-4 max-w-[82ch] border-t border-[var(--border)] pt-3 text-[12px] leading-[1.65] text-[var(--text-3)]">
      {children}
    </p>
  );
}

export function CaseLink({ id }: { id: string }) {
  return (
    <Link
      href={`/cases/${id}`}
      className="mono rounded-[var(--r-xs)] text-[var(--accent)] underline decoration-[var(--accent-line)] underline-offset-2 transition-colors hover:text-[var(--accent-2)] hover:decoration-[var(--accent)]"
    >
      {id}
    </Link>
  );
}

/** A proportion, drawn. Used where a rate would otherwise be a bare percentage and the
 *  reader has to compare it against the one two rows down by arithmetic. */
export function Bar({
  value,
  tone = "accent",
  label,
}: {
  value: number | null | undefined;
  tone?: Tone;
  label?: string;
}) {
  const v = value === null || value === undefined ? null : Math.max(0, Math.min(1, value));
  return (
    <span
      className="inline-flex h-[6px] w-full min-w-[52px] overflow-hidden rounded-full bg-[var(--surface-sunk)] align-middle"
      role="img"
      aria-label={label ?? (v === null ? "not measurable" : `${(v * 100).toFixed(1)} percent`)}
    >
      {v !== null && (
        <span className={`h-full rounded-full ${TONE[tone].dot}`} style={{ width: `${v * 100}%` }} />
      )}
    </span>
  );
}

/** A label above a group of panels, so a long page reads as sections rather than as a
 *  stack of unrelated boxes. */
export function SectionLabel({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mt-8 mb-3 first:mt-0">
      <div className="flex items-baseline gap-3">
        <h2 className="text-[12px] font-semibold tracking-[.05em] whitespace-nowrap text-[var(--text-3)] uppercase">
          {children}
        </h2>
        <span className="h-px grow bg-[var(--border)]" />
        {/* The aside drops below the rule on narrow screens rather than fighting the
            label for the same line — at 375px it was squeezing "The headline" onto two. */}
        {aside && (
          <span className="hidden text-[11.5px] text-[var(--text-3)] sm:inline">{aside}</span>
        )}
      </div>
      {aside && <p className="mt-1 text-[11.5px] text-[var(--text-3)] sm:hidden">{aside}</p>}
    </div>
  );
}

/**
 * A key/value list.
 *
 * The control room rendered its environment as a two-column `<Table>` with two empty
 * column headers, which is a definition list wearing a table's clothes — it announced
 * itself to a screen reader as a data table with unlabelled columns, and it inherited
 * table padding it did not want.
 */
export function KeyValue({ rows }: { rows: { k: ReactNode; v: ReactNode }[] }) {
  return (
    <dl className="grid gap-0 overflow-hidden rounded-[var(--r)] border border-[var(--border)]">
      {rows.map((r, i) => (
        <div
          key={i}
          className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--border)] px-3 py-[7px] last:border-0 odd:bg-[var(--surface-2)]"
        >
          <dt className="min-w-[140px] text-[12.5px] text-[var(--text-2)]">{r.k}</dt>
          <dd className="tnum min-w-0 text-[13px] font-medium">{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

const BUTTON_VARIANT = {
  primary:
    "border-transparent bg-[var(--accent)] text-[var(--on-accent)] hover:bg-[var(--accent-2)] shadow-[var(--shadow-1)]",
  default:
    "border-[var(--border-2)] bg-[var(--surface)] text-[var(--text)] hover:bg-[var(--surface-3)] hover:border-[var(--text-3)]",
  danger:
    "border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)] hover:bg-[var(--danger)] hover:text-[var(--on-accent)] hover:border-[var(--danger)]",
} as const;

/**
 * A button, with the variants a console actually needs.
 *
 * The control room previously drew three identical grey buttons, one of which drops the
 * database. Destructive and ordinary actions must not be the same object — the rule is
 * that a destructive control is visually separated and semantically marked, and the only
 * way to honour it is to have somewhere to put the distinction.
 *
 * `busy` disables and says why, rather than going grey and silent.
 */
export function Button({
  variant = "default",
  busy,
  disabled,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_VARIANT;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex min-h-[36px] items-center gap-2 rounded-[var(--r-sm)] border px-[13px] py-[7px] text-[13px] font-medium transition-colors duration-[var(--dur-fast)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-inherit ${BUTTON_VARIANT[variant]}`}
      {...rest}
    >
      {busy && (
        <span
          aria-hidden="true"
          className="h-[11px] w-[11px] shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  );
}
