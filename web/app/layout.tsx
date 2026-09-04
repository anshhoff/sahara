import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sahara — recovery console",
  description:
    "Failed subscription recovery, measured against a randomised control arm and net of what it cost.",
};

/**
 * Nine sections, matching the vanilla dashboard one for one — the parity gate in
 * task 5.5 says `dashboard/` is deleted only once every one of them has an equivalent
 * here, and until then both ship. Three of these have no equivalent over there at all:
 * the handoff queue, the fencing log and the promise timeline.
 */
const NAV: { href: string; label: string; note?: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/results", label: "Results" },
  { href: "/results/categories", label: "By cause" },
  { href: "/mechanism", label: "How it works" },
  { href: "/fencing", label: "Fencing", note: "new" },
  { href: "/promises", label: "Promises", note: "new" },
  { href: "/queue", label: "Handoff queue", note: "new" },
  { href: "/cases", label: "Cases" },
  { href: "/control", label: "Control room" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <div className="flex min-h-screen">
          <nav
            aria-label="Console sections"
            className="sticky top-0 hidden h-screen w-[var(--rail)] shrink-0 flex-col gap-1 overflow-y-auto border-r border-[var(--border)] bg-[var(--surface)] px-3 py-4 md:flex"
          >
            <Link href="/" className="mb-4 px-2">
              <div className="text-[15px] font-semibold tracking-tight">Sahara</div>
              <div className="text-[11px] leading-snug text-[var(--text-3)]">
                recovery console · every number traceable to case ids
              </div>
            </Link>
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="flex items-center gap-2 rounded-[var(--r-sm)] px-2 py-[7px] text-[13px] text-[var(--text-2)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
              >
                {item.label}
                {item.note && (
                  <span className="rounded-[4px] bg-[var(--accent-soft)] px-1 text-[10px] font-medium text-[var(--accent)]">
                    {item.note}
                  </span>
                )}
              </Link>
            ))}
            <div className="mt-auto px-2 pt-6 text-[11px] leading-relaxed text-[var(--text-3)]">
              All cases synthetic. Outcome probabilities are stated assumptions, not
              measured market data — what is measured here is the mechanism.
            </div>
          </nav>

          <main className="min-w-0 grow px-4 py-6 md:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
