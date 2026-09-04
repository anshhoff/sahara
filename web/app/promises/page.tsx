import { tryGet } from "@/lib/api";
import type { Promises } from "@/lib/types";
import { pct } from "@/lib/format";
import { CaseLink, Cell, Empty, Note, Panel, Row, Stat, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

export default async function PromisesPage() {
  const p = await tryGet<Promises>("/api/promises");
  if (!p) {
    return (
      <>
        <PageHeader title="Promises" />
        <ApiDown what="the promise tracker" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Promises"
        question="A date the customer actually named on a call — not a window we imposed and then called a promise."
      />

      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Promises made" value={p.n_promises} sub="dated, and extracted from Hinglish speech" />
        <Stat label="Kept" tone="ok" value={p.promises_kept} sub="paid on or before the day they named" />
        <Stat label="Broken" value={p.promises_broken} sub="the day passed unpaid; handed to a person" />
        <Stat label="Kept rate" value={p.kept_rate === null ? "—" : pct(p.kept_rate)} sub="of resolved promises" />
      </div>

      <div className="mt-4 grid gap-4">
        <Panel
          title="Who read them"
          lede={
            <>
              The rules are not a degraded model — they are a different reader with a
              measured accuracy of its own, and which one ran is recorded on every
              promise. On the column that decides anything (does the reading produce the
              same scheduling decision?) the model reads 90.0% against the keyword
              baseline&rsquo;s 60.0%, McNemar p = 0.0117.
            </>
          }
        >
          {Object.keys(p.by_reading_source).length === 0 ? (
            <Empty>No promises in this batch.</Empty>
          ) : (
            <Table head={["Reader", "open", "kept", "broken"]}>
              {Object.entries(p.by_reading_source).map(([source, counts]) => (
                <Row key={source}>
                  <Cell>{source}</Cell>
                  <Cell num>{counts.open ?? 0}</Cell>
                  <Cell num>{counts.kept ?? 0}</Cell>
                  <Cell num>{counts.broken ?? 0}</Cell>
                </Row>
              ))}
            </Table>
          )}
          <Note>{p.note}</Note>
        </Panel>

        {(["kept", "broken", "open"] as const).map((status) =>
          (p.case_ids[status] ?? []).length ? (
            <Panel key={status} title={`${status} (${p.case_ids[status].length})`}>
              <div className="flex flex-wrap gap-2">
                {p.case_ids[status].map((id) => (
                  <CaseLink key={id} id={id} />
                ))}
              </div>
            </Panel>
          ) : null,
        )}
      </div>
    </>
  );
}
