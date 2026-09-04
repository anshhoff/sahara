"use client";

/**
 * The console rail, and on small screens the drawer that stands in for it.
 *
 * This is the one client component in the shell, and it is a client component for
 * exactly two reasons: `usePathname`, because a navigation that does not say where you
 * are is not navigation; and the drawer, because the previous rail was `hidden md:flex`
 * with nothing behind it — below 768px this console had no way to get from one page to
 * another at all.
 *
 * The links are grouped rather than listed. Nine flat items make the reader scan all
 * nine every time; three groups of three make them scan one. The grouping is the actual
 * shape of the console: what the agent achieved, how it works, and the records behind
 * both.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ComponentType, type SVGProps } from "react";
import {
  IconBolt,
  IconCases,
  IconCause,
  IconClose,
  IconControl,
  IconFencing,
  IconMechanism,
  IconMenu,
  IconOverview,
  IconPromise,
  IconQueue,
  IconResults,
} from "./icons";

type Item = {
  href: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;
  note?: string;
  /** What this page answers. Shown in the drawer, and as the link's title on the rail. */
  hint: string;
};

/**
 * Ten sections. Nine match the vanilla dashboard one for one — the parity gate in task
 * 5.5 says `dashboard/` is deleted only once every one of them has an equivalent here,
 * and until then both ship. Three have no equivalent over there at all: the handoff
 * queue, the fencing log and the promise timeline.
 *
 * Test mode is the tenth and it closes the last parity gap: `dashboard/live-demo.html`,
 * `subscribe.html` and `live-pipeline.html` had no home in this console, which meant the
 * one surface that runs a REAL Razorpay failure through the pipeline lived only in the
 * page this console was meant to replace.
 */
const GROUPS: { label: string; items: Item[] }[] = [
  {
    label: "Measure",
    items: [
      { href: "/", label: "Overview", icon: IconOverview, hint: "The headline, net of cost" },
      { href: "/results", label: "Results", icon: IconResults, hint: "Primary and secondary metrics" },
      { href: "/results/categories", label: "By cause", icon: IconCause, hint: "Lift split by failure cause" },
    ],
  },
  {
    label: "Mechanism",
    items: [
      { href: "/mechanism", label: "How it works", icon: IconMechanism, hint: "The policy table and the gates" },
      { href: "/fencing", label: "Fencing", icon: IconFencing, note: "new", hint: "Outreach the agent blocked" },
      { href: "/promises", label: "Promises", icon: IconPromise, note: "new", hint: "Dates customers named" },
      {
        href: "/demo",
        label: "Test mode",
        icon: IconBolt,
        hint: "Run one real failure through it",
      },
    ],
  },
  {
    label: "Records",
    items: [
      { href: "/queue", label: "Handoff queue", icon: IconQueue, note: "new", hint: "Cases handed to a person" },
      { href: "/cases", label: "Cases", icon: IconCases, hint: "Every case, and its audit trail" },
      { href: "/control", label: "Control room", icon: IconControl, hint: "Replay and inspect" },
    ],
  },
];

/** `/results` must not light up while you are on `/results/categories`. */
function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  if (href === "/results") return pathname === "/results";
  return pathname === href || pathname.startsWith(href + "/");
}

function NavLink({
  item,
  active,
  onNavigate,
  showHint,
}: {
  item: Item;
  active: boolean;
  onNavigate?: () => void;
  showHint?: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={showHint ? undefined : item.hint}
      className={`group relative flex items-start gap-[10px] rounded-[var(--r-sm)] px-[10px] py-[7px] text-[13px] transition-colors duration-[var(--dur-fast)] ${
        active
          ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent)]"
          : "font-medium text-[var(--text-2)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
      }`}
    >
      {/* The active marker is a bar, not only a colour — the rule against colour
          carrying meaning on its own applies to navigation too. */}
      <span
        aria-hidden="true"
        className={`absolute top-1/2 left-0 h-[15px] w-[3px] -translate-y-1/2 rounded-r-full bg-[var(--accent)] transition-opacity duration-[var(--dur-fast)] ${
          active ? "opacity-100" : "opacity-0"
        }`}
      />
      <Icon
        size={16}
        className={`mt-[1px] shrink-0 ${active ? "text-[var(--accent)]" : "text-[var(--text-3)] group-hover:text-[var(--text-2)]"}`}
      />
      <span className="min-w-0 grow">
        <span className="flex items-center gap-[6px]">
          <span className="truncate">{item.label}</span>
          {item.note && (
            <span className="rounded-[var(--r-xs)] border border-[var(--accent-line)] bg-[var(--surface)] px-[4px] text-[10px] leading-[15px] font-semibold tracking-wide text-[var(--accent)] uppercase">
              {item.note}
            </span>
          )}
        </span>
        {showHint && (
          <span className="mt-[1px] block text-[11px] leading-snug font-normal text-[var(--text-3)]">
            {item.hint}
          </span>
        )}
      </span>
    </Link>
  );
}

function Brand({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Link
      href="/"
      onClick={onNavigate}
      className="flex items-center gap-[9px] rounded-[var(--r-sm)] px-[10px] py-1"
    >
      {/* The mark: a charge that failed and came back. Drawn rather than loaded, so
          it is one fewer request and it inherits the accent. */}
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="shrink-0">
        <rect width="24" height="24" rx="7" fill="var(--accent)" />
        <path
          d="M7 14.5c1.6-5.2 3.4-5.2 5 0 1.6 5.2 3.4 5.2 5 0"
          fill="none"
          stroke="var(--on-accent)"
          strokeWidth="1.9"
          strokeLinecap="round"
        />
      </svg>
      <span className="min-w-0">
        <span className="block text-[15px] leading-tight font-semibold tracking-tight">Sahara</span>
        <span className="block text-[11px] leading-tight text-[var(--text-3)]">recovery console</span>
      </span>
    </Link>
  );
}

function Footnote() {
  return (
    <p className="px-[10px] text-[11px] leading-relaxed text-[var(--text-3)]">
      All cases synthetic. Outcome probabilities are stated assumptions, not measured
      market data — what is measured here is the mechanism.
    </p>
  );
}

export function Rail() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Console sections"
      data-print-hide
      className="sticky top-0 hidden h-screen w-[var(--rail)] shrink-0 flex-col overflow-y-auto border-r border-[var(--border)] bg-[var(--surface)] px-3 py-4 md:flex"
    >
      <Brand />
      <div className="mt-5 flex flex-col gap-5">
        {GROUPS.map((g) => (
          <div key={g.label}>
            <div className="eyebrow mb-[6px] px-[10px]">{g.label}</div>
            <div className="flex flex-col gap-[2px]">
              {g.items.map((item) => (
                <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-auto border-t border-[var(--border)] pt-4">
        <Footnote />
      </div>
    </nav>
  );
}

export function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const current = GROUPS.flatMap((g) => g.items).find((i) => isActive(pathname, i.href));

  // Escape closes, and the page behind stops scrolling while the drawer owns the screen.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <>
      <header
        data-print-hide
        className="sticky top-0 z-30 flex h-[var(--topbar)] items-center gap-2 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] px-3 backdrop-blur-md md:hidden"
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          aria-label="Open console sections"
          className="-ml-1 flex h-11 w-11 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-2)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          <IconMenu size={20} />
        </button>
        <Brand />
        {current && (
          <span className="ml-auto truncate pl-2 text-[12px] font-medium text-[var(--text-3)]">
            {current.label}
          </span>
        )}
      </header>

      {open && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Console sections">
          <button
            type="button"
            aria-label="Close console sections"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-[rgba(17,22,36,.45)] backdrop-blur-[2px]"
          />
          <div
            ref={panelRef}
            tabIndex={-1}
            className="absolute inset-y-0 left-0 flex w-[min(310px,86vw)] flex-col overflow-y-auto bg-[var(--surface)] px-3 py-4 shadow-[var(--shadow-3)] outline-none"
          >
            <div className="flex items-center">
              <Brand onNavigate={() => setOpen(false)} />
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close console sections"
                className="ml-auto flex h-11 w-11 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-2)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
              >
                <IconClose size={18} />
              </button>
            </div>
            <div className="mt-4 flex flex-col gap-5">
              {GROUPS.map((g) => (
                <div key={g.label}>
                  <div className="eyebrow mb-[6px] px-[10px]">{g.label}</div>
                  <div className="flex flex-col gap-[2px]">
                    {g.items.map((item) => (
                      <NavLink
                        key={item.href}
                        item={item}
                        active={isActive(pathname, item.href)}
                        onNavigate={() => setOpen(false)}
                        showHint
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-auto border-t border-[var(--border)] pt-4">
              <Footnote />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
