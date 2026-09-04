import { tryGet } from "@/lib/api";
import type { Fencing } from "@/lib/types";
import { when, words } from "@/lib/format";
import { Badge, Cell, CaseLink, Empty, Note, Panel, Row, Stat, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

/** One of the three surfaces the vanilla dashboard has no equivalent for (task 5.4). */
const PHASES: [string, string, string][] = [
  [
    "pre_dispatch",
    "Before dispatch",
    "Re-fetch the subscription immediately before any contact leaves. A blocking verdict stops the cycle, and no attempt is spent — nothing forbade the message, there was simply nothing left to collect.",
  ],
  [
    "post_dispatch",
    "After the write",
    "Re-fetch once a payment link exists. Too late not to create it; not too late to cancel it and say so. The compensation entry is appended whether or not the cancellation succeeded.",
  ],
  [
    "inference",
    "Around the model call",
    "A SHA-256 over decision-relevant fields only, taken before the call and rechecked after. Notes, timestamps and customer metadata never move it — a guard that fires on irrelevant churn is switched off within a week.",
  ],
];

export default async function FencingPage() {
  const f = await tryGet<Fencing>("/api/fencing");
  if (!f) {
    return (
      <>
        <PageHeader title="Dispatch fencing" />
        <ApiDown what="the fencing log" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Dispatch fencing"
        question="Every invariant re-reads the case — our record of the world, exactly as stale as the last webhook that arrived. These three re-read the world."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label="Outreach to already-settled customers"
          tone={f.outreach_to_settled ? undefined : "ok"}
          value={f.outreach_to_settled}
          sub={`of ${f.n_dispatches_fenced} dispatches fenced — a zero with no denominator attests to nothing`}
        />
        <Stat
          label="Cases stopped as already settled"
          value={f.stopped_already_settled}
          sub="a correctness stop, not a safety one: the money had already arrived"
        />
        <Stat
          label="Compensation entries"
          value={f.n_compensations}
          sub="appended whether or not the link cancellation succeeded"
        />
      </div>

      <div className="mt-4 grid gap-4">
        <Panel
          title="The three fences"
          lede={
            <>
              A fence <strong>degrades, it never raises</strong>. A transport fault inside
              one is caught, logged and reported as data — a fence that can crash the
              pipeline is strictly worse than no fence at all, and test-mode rate limits
              make that a real path. When it cannot reach the truth it records{" "}
              <span className="mono">unverified</span>, which does not block, so
              &ldquo;we did not check&rdquo; is never mistaken for &ldquo;we checked and it
              was fine&rdquo;.
            </>
          }
        >
          <Table head={["Fence", "clear", "settled", "changed", "unverified"]}>
            {PHASES.map(([key, label, blurb]) => {
              const v = f.by_phase[key] ?? {};
              return (
                <Row key={key}>
                  <Cell>
                    <div className="font-medium">{label}</div>
                    <div className="max-w-[60ch] text-[12px] leading-relaxed text-[var(--text-3)]">
                      {blurb}
                    </div>
                  </Cell>
                  <Cell num>{v.clear ?? 0}</Cell>
                  <Cell num>{v.settled ?? 0}</Cell>
                  <Cell num>{v.changed ?? 0}</Cell>
                  <Cell num>
                    {v.unverified ? <Badge tone="warn">{v.unverified}</Badge> : 0}
                  </Cell>
                </Row>
              );
            })}
          </Table>
          <Note>{f.note}</Note>
        </Panel>

        <Panel title="Compensation log" aside="what happened after the system found out it had acted on a stale world">
          {f.compensations.length === 0 ? (
            <Empty>
              No dispatch in this batch turned out to be wrong after the fact. The log is
              empty, not absent — and the denominator above says how many dispatches were
              checked.
            </Empty>
          ) : (
            <Table head={["Case", "seq", "What was done", "When"]}>
              {f.compensations.map((c) => (
                <Row key={`${c.case_id}-${c.seq}`}>
                  <Cell>
                    <CaseLink id={c.case_id} />
                  </Cell>
                  <Cell num>{c.seq}</Cell>
                  <Cell>{c.summary}</Cell>
                  <Cell className="text-[var(--text-3)]">{when(c.created_at)}</Cell>
                </Row>
              ))}
            </Table>
          )}
        </Panel>

        {f.outreach_to_settled_case_ids.length > 0 && (
          <Panel title="Breaches">
            <div className="mono flex flex-wrap gap-2">
              {f.outreach_to_settled_case_ids.map((id) => (
                <CaseLink key={id} id={id} />
              ))}
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}
