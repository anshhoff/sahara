/**
 * The shape of a page that has not arrived yet.
 *
 * Every read page in this console is `force-dynamic` and waits on the API, so until now
 * a navigation showed the previous page frozen until the new one was fully rendered —
 * on a cold API that is several seconds of a console that appears to have ignored the
 * click. A skeleton is not decoration here; it is the acknowledgement.
 *
 * Deliberately grey and deliberately unlabelled. A skeleton that guesses at numbers
 * would break the one rule this project actually has: never render a figure that is not
 * a measurement.
 */
export function Shimmer({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-[var(--r-sm)] bg-[var(--surface-3)] ${className}`}
      aria-hidden="true"
    />
  );
}

export function PageSkeleton({ stats = 4, panels = 2 }: { stats?: number; panels?: number }) {
  return (
    <div role="status" aria-label="Loading">
      <span className="sr-only">Loading</span>
      <Shimmer className="h-[13px] w-[92px]" />
      <Shimmer className="mt-3 h-[30px] w-[210px]" />
      <Shimmer className="mt-3 h-[15px] w-full max-w-[560px]" />

      <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: stats }, (_, i) => (
          <div
            key={i}
            className="rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)] px-4 py-[14px]"
          >
            <Shimmer className="h-[11px] w-[96px]" />
            <Shimmer className="mt-3 h-[26px] w-[124px]" />
            <Shimmer className="mt-3 h-[11px] w-full" />
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {Array.from({ length: panels }, (_, i) => (
          <div
            key={i}
            className="overflow-hidden rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)]"
          >
            <div className="border-b border-[var(--border)] bg-[var(--surface-2)] px-4 py-[11px]">
              <Shimmer className="h-[13px] w-[132px]" />
            </div>
            <div className="flex flex-col gap-[14px] px-4 py-4">
              {Array.from({ length: 5 }, (_, r) => (
                <Shimmer key={r} className="h-[13px]" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
