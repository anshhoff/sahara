"use client";

/**
 * Which chrome a route gets.
 *
 * Three of the demo routes are not console pages and must not look like one.
 * `/subscribe` is a customer staring at a checkout that just failed — it is a different
 * product with a different name on it, and putting a nine-item operator rail down its
 * left-hand side would be a lie about who it is for. `/pay` is the far end of that same
 * story: the page the recovery link opens, seen by someone who has never heard of this
 * console and must not be shown its internals. `/pipeline` is the companion window the
 * subscribe page pops open; it is an operator surface, but it is a *panel*, opened
 * beside the thing it watches, so it carries a title bar rather than the rail.
 *
 * Decided here rather than by moving all eleven existing pages into a `(console)` route
 * group: the group is the more idiomatic App Router answer, but it renames every path in
 * the repo to add two pages, and the rail was already a client component reading the
 * pathname for its active state.
 */
import { usePathname } from "next/navigation";
import { MobileNav, Rail } from "./Nav";

/** Routes that get no operator chrome at all. */
const BARE = ["/subscribe", "/pipeline", "/pay"];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const bare = BARE.some((p) => pathname === p || pathname.startsWith(p + "/"));

  if (bare) {
    return (
      <main id="main" tabIndex={-1} className="min-h-dvh outline-none">
        {children}
      </main>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <Rail />
      <MobileNav />
      <main id="main" tabIndex={-1} className="min-w-0 grow px-4 py-6 outline-none md:px-8 md:py-8">
        {/* The column is bounded. Unbounded, a paragraph of explanation on a 27in
            monitor runs to two hundred characters a line and nobody reads it. */}
        <div className="mx-auto w-full max-w-[var(--content-max)]">{children}</div>
      </main>
    </div>
  );
}
