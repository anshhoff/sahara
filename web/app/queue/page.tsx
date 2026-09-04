import { tryGet } from "@/lib/api";
import type { CaseRow, Summary } from "@/lib/types";
import { rupees, when, words } from "@/lib/format";
import { Badge, CaseLink, Cell, Empty, Note, Panel, Row, Stat, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

/**
 * The handoff queue (task 5.4).
 *
 * We charge ₹40 a case for this and have never had a screen for it — which meant the
 * most expensive line in the cost table was the only one nobody could look at. Counting
 * a handoff is what stops "hand it off" from being a costless way to make a hard case
 * disappear; showing the queue is what stops the count from being an abstraction.
 */
const QUEUE_STATUSES = [
  "stopped_handoff",
  "stopped_max_attempts",
  "stopped_cooldown_expired",
  "stopped_unknown",
] as const;

export default async function QueuePage() {
  const [s, ...lists] = await Promise.all([
    tryGet<Summary>("/api/summary"),
    ...QUEUE_STATUSES.map((status) => tryGet<CaseRow[]>("/api/cases", { status })),
  ]);
  if (!s || lists.some((l) => l === null)) {
    return (
      <>
        <PageHeader title="Handoff queue" />
        <ApiDown what="the handoff queue" />
      </>
    );
  }

  const rows = (lists as CaseRow[][]).flat().sort((a, b) => (a.closed_at ?? "").localeCompare(b.closed_at ?? ""));
  const unitCost = s.costs.unit_costs_paise.STOP_HANDOFF ?? 0;

  return (
    <>
      <PageHeader
        title="Handoff queue"
        question="The cases a person has to work — and what they cost, so that handing one over is never free."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Cases waiting for a person" value={rows.length} sub="every one has a complete case file" />
        <Stat label="Cost of this queue" value={rupees(s.costs.handoff_paise)} sub={`${rupees(unitCost)} per case, booked in the net figure`} />
        <Stat label="₹ still at risk here" value={rupees(rows.reduce((t, r) => t + r.amount_at_risk_paise, 0))} sub="one charge cycle per case, frozen at case creation" />
      </div>

      <div className="mt-4">
        <Panel
          title="The queue"
          aside="oldest first"
          lede={
            <>
              A handoff moves no money and contacts nobody, but it is not free — it puts a
              case in front of a person. A system that could make any hard case disappear
              at zero cost by handing it off would be measuring the wrong thing, so every
              row here is charged {rupees(unitCost)} in the cost table. Each case id opens
              its complete file: that response <em>is</em> the handoff artifact.
            </>
          }
        >
          {rows.length === 0 ? (
            <Empty>Nothing is waiting for a person in this batch.</Empty>
          ) : (
            <Table head={["Case", "Cause", "Why it is here", "Attempts", "₹ at risk", "Closed"]}>
              {rows.map((c) => (
                <Row key={c.case_id}>
                  <Cell>
                    <CaseLink id={c.case_id} />
                  </Cell>
                  <Cell>{words(c.category)}</Cell>
                  <Cell>
                    <Badge tone="warn">{words(c.status)}</Badge>
                  </Cell>
                  <Cell num>{c.attempt_count}</Cell>
                  <Cell num>{rupees(c.amount_at_risk_paise)}</Cell>
                  <Cell className="text-[var(--text-3)]">{when(c.closed_at)}</Cell>
                </Row>
              ))}
            </Table>
          )}
          <Note>
            <span className="mono">stopped_unknown</span> is in this queue on purpose. The
            agent refused to guess a cause it could not diagnose (I4), which is the correct
            behaviour and also exactly the case a human is better at than a rule table.
          </Note>
        </Panel>
      </div>
    </>
  );
}
