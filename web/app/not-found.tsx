import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";
import { Empty } from "@/components/ui";

/**
 * Reached mainly by a case id that does not exist — a stale link out of someone's
 * notes, or a batch that has since been replayed from a different seed. That second one
 * is the likely cause and is worth saying, because it is not an error.
 */
export default function NotFound() {
  return (
    <>
      <PageHeader
        title="Not found"
        eyebrow="404"
        question="Nothing on this console answers to that address."
      />
      <Empty>
        If this was a case id, the batch may have been replayed since the link was made —
        replaying regenerates every case from the seed and issues new ids.
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Link
            href="/cases"
            className="rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-3 py-[7px] text-[13px] font-medium transition-colors hover:bg-[var(--surface-3)]"
          >
            Browse all cases
          </Link>
          <Link
            href="/"
            className="rounded-[var(--r-sm)] border border-transparent bg-[var(--accent)] px-3 py-[7px] text-[13px] font-medium text-[var(--on-accent)] transition-colors hover:bg-[var(--accent-2)]"
          >
            Back to the overview
          </Link>
        </div>
      </Empty>
    </>
  );
}
