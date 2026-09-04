import Link from "next/link";
import type { ReactNode } from "react";
import { IconChevronRight } from "./icons";

export type Crumb = { href: string; label: string };

/**
 * The top of every page.
 *
 * `question` is the thing worth keeping from the old header: each page states the
 * question it answers rather than describing itself. It now reads as a standfirst —
 * larger than body, narrower than the page — instead of as another line of grey UI text
 * indistinguishable from the panel ledes below it.
 *
 * Breadcrumbs exist for the two pages that sit three deep (`/results/categories` and
 * `/cases/{id}`), where the rail highlights a section but cannot say which record
 * inside it you are looking at.
 */
export function PageHeader({
  title,
  /** Set the title in the mono face — for identifiers (case ids), never for prose. */
  monoTitle,
  eyebrow,
  question,
  crumbs,
  actions,
  children,
}: {
  title: string;
  monoTitle?: boolean;
  eyebrow?: string;
  question?: string;
  crumbs?: Crumb[];
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-6">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2">
          <ol className="flex flex-wrap items-center gap-1 text-[12px] text-[var(--text-3)]">
            {crumbs.map((c) => (
              <li key={c.href} className="flex items-center gap-1">
                <Link
                  href={c.href}
                  className="rounded-[var(--r-xs)] transition-colors hover:text-[var(--accent)]"
                >
                  {c.label}
                </Link>
                <IconChevronRight size={12} className="text-[var(--border-2)]" />
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0">
          {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
          <h1
            className={
              monoTitle
                ? "mono text-[clamp(15px,2.2vw,20px)] leading-tight font-semibold break-all"
                : "text-[clamp(22px,3vw,var(--t-2xl))] leading-tight font-semibold tracking-[-0.02em]"
            }
          >
            {title}
          </h1>
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto">{actions}</div>
        )}
      </div>
      {question && (
        <p className="mt-[10px] max-w-[80ch] text-[14.5px] leading-[1.6] text-[var(--text-2)]">
          {question}
        </p>
      )}
      {children}
    </header>
  );
}
