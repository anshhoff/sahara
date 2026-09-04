import { tryGet } from "@/lib/api";
import type { CaseRow } from "@/lib/types";
import { rupees, when, words, STATUS_TONE } from "@/lib/format";
import { Badge, CaseLink, Cell, Empty, Note, Panel, Row, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

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

  const filter = [status && `status = ${status}`, category && `cause = ${category}`]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <PageHeader
        title="Cases"
        question="Every headline number on this console resolves to a list of these, and every one of them to an audit trail."
      />
      <Panel
        title={`${rows.length} cases`}
        aside={filter || "all"}
        lede="The control arm carries its own colour here and everywhere else in this console — the one place colour means something, and it always ships with the word next to it."
      >
        {rows.length === 0 ? (
          <Empty>No cases match this filter.</Empty>
        ) : (
          <Table head={["Case", "Arm", "Cause", "Status", "Attempts", "₹ at risk", "Opened"]}>
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
                <Cell>{words(c.category)}</Cell>
                <Cell>
                  <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{words(c.status)}</Badge>
                </Cell>
                <Cell num>{c.attempt_count}</Cell>
                <Cell num>{rupees(c.amount_at_risk_paise)}</Cell>
                <Cell className="text-[var(--text-3)]">{when(c.created_at)}</Cell>
              </Row>
            ))}
          </Table>
        )}
        <Note>
          A case is <span className="mono">recovered</span> only on a real recovery signal.
          Sending a link recovers nothing.
        </Note>
      </Panel>
    </>
  );
}
