import Link from "next/link";
import { tryGet } from "@/lib/api";
import type { CaseRow } from "@/lib/types";
import { rupees, when, words, STATUS_TONE } from "@/lib/format";
import { Badge, CaseLink, Cell, Empty, Note, Panel, Row, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";
import { IconClose } from "@/components/icons";

export const dynamic = "force-dynamic";

/**
 * A filter that is applied but invisible.
 *
 * Every cause on `/results/categories` links here with `?category=…`, and until now the
 * only trace of that on arrival was that the list was shorter than you expected. The
 * filter is now stated, and it is removable — a filter you cannot see is a bug report
 * about missing data waiting to happen.
 */
function FilterChip({ label, value, clearHref }: { label: string; value: string; clearHref: string }) {
  return (
    <span className="inline-flex items-center gap-[6px] rounded-full border border-[var(--accent-line)] bg-[var(--accent-soft)] py-[2px] pr-[3px] pl-[9px] text-[12px] text-[var(--accent)]">
      <span className="text-[var(--text-2)]">{label}</span>
      <strong className="font-semibold">{words(value)}</strong>
      <Link
        href={clearHref}
        aria-label={`Remove the ${label} filter`}
        className="flex h-[18px] w-[18px] items-center justify-center rounded-full transition-colors hover:bg-[var(--accent-line)]"
      >
        <IconClose size={11} />
      </Link>
    </span>
  );
}

export default async function CasesPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; category?: string }>;
}) {
  const { status, category } = await searchParams;
  const rows = await tryGet<CaseRow[]>("/api/cases", { status, category });
  if (!rows) {
    return (
      <>
        <PageHeader title="Cases" />
        <ApiDown what="the case list" />
      </>
    );
  }

  const q = (next: { status?: string | null; category?: string | null }) => {
    const p = new URLSearchParams();
    if (next.status) p.set("status", next.status);
    if (next.category) p.set("category", next.category);
    const s = p.toString();
    return s ? `/cases?${s}` : "/cases";
  };

  const filtered = Boolean(status || category);
  const atRisk = rows.reduce((t, r) => t + r.amount_at_risk_paise, 0);
  const nControl = rows.filter((r) => r.is_holdout).length;

  return (
    <>
      <PageHeader
        title="Cases"
        eyebrow="Records"
        question="Every headline number on this console resolves to a list of these, and every one of them to an audit trail."
        actions={
          <span className="tnum text-[12px] text-[var(--text-3)]">
            {rupees(atRisk)} at risk · {nControl} in the control arm
          </span>
        }
      >
        {filtered && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="eyebrow">Filtered by</span>
            {status && (
              <FilterChip label="status" value={status} clearHref={q({ category })} />
            )}
            {category && (
              <FilterChip label="cause" value={category} clearHref={q({ status })} />
            )}
            <Link
              href="/cases"
              className="rounded-[var(--r-xs)] text-[12px] text-[var(--text-3)] underline underline-offset-2 transition-colors hover:text-[var(--accent)]"
            >
              clear all
            </Link>
          </div>
        )}
      </PageHeader>

      <Panel
        title={`${rows.length} ${rows.length === 1 ? "case" : "cases"}`}
        aside={filtered ? "filtered" : "all cases in this batch"}
        flush
        lede="The control arm carries its own colour here and everywhere else in this console — the one place colour means something, and it always ships with the word next to it."
      >
        {rows.length === 0 ? (
          <div className="px-4 py-4">
            <Empty>
              No cases match this filter.{" "}
              <Link
                href="/cases"
                className="text-[var(--accent)] underline decoration-[var(--accent-line)] underline-offset-2 hover:decoration-[var(--accent)]"
              >
                Clear it
              </Link>{" "}
              to see the whole batch.
            </Empty>
          </div>
        ) : (
          <Table
            head={[
              "Case",
              "Arm",
              "Cause",
              "Status",
              { label: "Attempts", num: true },
              { label: "₹ at risk", num: true },
              "Opened",
            ]}
          >
            {rows.map((c) => (
              <Row key={c.case_id}>
                <Cell>
                  <CaseLink id={c.case_id} />
                </Cell>
                <Cell>
                  {c.is_holdout ? (
                    <Badge tone="control">control</Badge>
                  ) : (
                    <span className="text-[var(--text-3)]">treated</span>
                  )}
                </Cell>
                <Cell>
                  <Link
                    href={q({ status, category: c.category })}
                    className="rounded-[var(--r-xs)] transition-colors hover:text-[var(--accent)] hover:underline"
                  >
                    {words(c.category)}
                  </Link>
                </Cell>
                <Cell>
                  <Link href={q({ status: c.status, category })} className="rounded-[var(--r-xs)]">
                    <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{words(c.status)}</Badge>
                  </Link>
                </Cell>
                <Cell num>{c.attempt_count}</Cell>
                <Cell num>{rupees(c.amount_at_risk_paise)}</Cell>
                <Cell className="whitespace-nowrap text-[var(--text-3)]">{when(c.created_at)}</Cell>
              </Row>
            ))}
          </Table>
        )}
        <div className="px-4 pb-4">
          <Note>
            A case is <span className="mono">recovered</span> only on a real recovery signal.
            Sending a link recovers nothing. Any cause or status in this table narrows the
            list to it.
          </Note>
        </div>
      </Panel>
    </>
  );
}
