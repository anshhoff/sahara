import { tryGet } from "@/lib/api";
import type { Promises, VoiceCalls } from "@/lib/types";
import { pct } from "@/lib/format";
import { Badge, CaseLink, Cell, Empty, Note, Panel, Row, Stat, Table } from "@/components/ui";
import type { Tone } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

// A promise is one outcome of a call, not the only one. `no_answer`, `cannot_pay` and
// `wrong_number` produce no row in the promise table and are not failures of the
// reader — so the call table below carries its own tone map rather than borrowing the
// promise one, which would paint every non-promise red.
const PROMISE_TONE: Record<string, Tone> = {
  kept: "ok",
  broken: "danger",
  open: "warn",
};

export default async function PromisesPage() {
  const [p, v] = await Promise.all([
    tryGet<Promises>("/api/promises"),
    tryGet<VoiceCalls>("/api/voice-calls"),
  ]);
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
            <Table
              head={[
                "Reader",
                { label: "open", num: true },
                { label: "kept", num: true },
                { label: "broken", num: true },
              ]}
            >
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

        {v && (
          <Panel
            title="The calls behind them"
            aside={`${v.n_promised} of ${v.n_calls} calls produced a promise`}
            flush
            lede={
              <>
                Every voice call placed, in the register these calls are actually
                conducted in. The seven intents that make no promise are shown alongside
                the one that does, because a table that listed only the readings which
                produced a date would be showing the reader nothing but its own
                successes. {v.n_answered} of {v.n_calls} calls were answered.
              </>
            }
            footer={<Note>{v.note}</Note>}
          >
            {v.calls.length === 0 ? (
              <Empty>No voice calls in this batch — voice is rung 3, so a batch whose cases never reach a third attempt has none.</Empty>
            ) : (
              <Table
                head={[
                  { label: "Case", w: "18%" },
                  { label: "What they said", w: "38%" },
                  "Intent",
                  "Reader",
                  "Date named",
                  "Promise",
                ]}
              >
                {v.calls.map((c) => (
                  <Row key={c.execution_id}>
                    <Cell>
                      <CaseLink id={c.case_id} />
                    </Cell>
                    <Cell className="italic">
                      {c.transcript ?? <span className="not-italic opacity-60">— nobody answered —</span>}
                    </Cell>
                    <Cell className="mono">{c.intent ?? "—"}</Cell>
                    <Cell className="mono">{c.source ?? "—"}</Cell>
                    <Cell className="mono">{c.promised_date ?? "—"}</Cell>
                    <Cell>
                      {c.promise_status ? (
                        <Badge tone={PROMISE_TONE[c.promise_status] ?? "neutral"}>
                          {c.promise_status}
                        </Badge>
                      ) : (
                        <span className="opacity-60">—</span>
                      )}
                    </Cell>
                  </Row>
                ))}
              </Table>
            )}
          </Panel>
        )}

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
