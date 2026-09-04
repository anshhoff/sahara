import { tryGet } from "@/lib/api";
import type { Summary } from "@/lib/types";
import { hours, pct, pp, rupees, words } from "@/lib/format";
import { Badge, Cell, Empty, Note, Panel, Row, Stat, Table } from "@/components/ui";
import { Interval } from "@/components/CI";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const s = await tryGet<Summary>("/api/summary");
  if (!s) {
    return (
      <>
        <PageHeader title="Overview" />
        <ApiDown what="the summary" />
      </>
    );
  }

  const inc = s.incremental;
  const net = s.net;

  return (
    <>
      <PageHeader
        title="Overview"
        question="How much money came back because of the agent — not alongside it — and what did getting it cost?"
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Net incremental recovery"
          tone="ok"
          value={net.incremental_available ? rupees(net.net_incremental_paise) : "—"}
          sub={
            net.incremental_available && net.net_incremental_paise_ci95 ? (
              <>
                95% CI [{rupees(net.net_incremental_paise_ci95[0])},{" "}
                {rupees(net.net_incremental_paise_ci95[1])}] · the one number this project
                stands behind
              </>
            ) : (
              "no control arm in this batch"
            )
          }
        />
        <Stat
          label="Lift, treated vs control"
          value={inc.available ? pp(inc.lift) : "—"}
          sub={
            inc.available ? (
              <>
                {pct(inc.treated.rate)} vs {pct(inc.control.rate)} · 95% CI [
                {pp(inc.lift_ci95[0])}, {pp(inc.lift_ci95[1])}]
              </>
            ) : (
              inc.reason
            )
          }
        />
        <Stat
          label="Deliberately not contacted"
          value={s.declined_to_contact.n_declined}
          sub={`cases the agent could have messaged and refused to · ${s.declined_to_contact.n_control_arm} more held out to measure the rest`}
        />
        <Stat
          label="Outreach to already-settled"
          value={s.fencing.outreach_to_settled}
          sub={`of ${s.fencing.n_dispatches_fenced} dispatches fenced — a zero without a denominator attests to nothing`}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel
          title="Treated vs control"
          aside="intention-to-treat"
          lede={
            <>
              Gross recovery cannot separate money the agent won from money that would
              have come back anyway. A randomised slice is held out — detected and
              diagnosed like any other case, then never intervened on — and the gap
              between the arms is the effect.
            </>
          }
        >
          {!inc.available ? (
            <Empty>{inc.reason}</Empty>
          ) : (
            <>
              <Table head={["Arm", "n", "Recovered", "Rate", "Organic"]}>
                <Row>
                  <Cell>treated</Cell>
                  <Cell num>{inc.treated.n}</Cell>
                  <Cell num>{inc.treated.recovered}</Cell>
                  <Cell num>{pct(inc.treated.rate)}</Cell>
                  <Cell num>
                    {inc.treated.organic ?? "—"}{" "}
                    <span className="text-[var(--text-3)]">
                      {inc.treated.organic_rate != null && `(${pct(inc.treated.organic_rate)})`}
                    </span>
                  </Cell>
                </Row>
                <Row>
                  <Cell>
                    <Badge tone="control">control</Badge>
                  </Cell>
                  <Cell num>{inc.control.n}</Cell>
                  <Cell num>{inc.control.recovered}</Cell>
                  <Cell num>{pct(inc.control.rate)}</Cell>
                  <Cell num>
                    {inc.control.organic ?? "—"}{" "}
                    <span className="text-[var(--text-3)]">
                      {inc.control.organic_rate != null && `(${pct(inc.control.organic_rate)})`}
                    </span>
                  </Cell>
                </Row>
              </Table>
              <div className="mt-4 border-t border-[var(--border)] pt-3">
                <Interval
                  value={inc.lift}
                  ci={inc.lift_ci95}
                  significant={inc.significant}
                  format={(x) => pp(x)}
                />
              </div>
              <Note>
                The control arm is <strong>not silence.</strong> It reproduces Razorpay&rsquo;s
                own retry schedule plus organic self-cure, so the lift is measured against
                what the merchant would have got by changing nothing at all. Organic counts
                are censored by treatment — a treated case that would have self-cured on day
                nine often recovers on day two first — so the balance evidence is the roll,
                not the outcome.
              </Note>
            </>
          )}
        </Panel>

        <Panel title="Net of what it cost" aside={`${s.n_cases} cases · seed ${s.seed ?? "—"}`}>
          <Table head={["", "", ""]}>
            <Row>
              <Cell>Gross recovered</Cell>
              <Cell num>{rupees(net.gross_recovered_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">every rupee that came back</Cell>
            </Row>
            <Row>
              <Cell>Outreach</Cell>
              <Cell num>− {rupees(s.costs.outreach_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">
                {Object.entries(s.executions_by_action)
                  .filter(([, n]) => n)
                  .map(([a, n]) => `${n} ${words(a).toLowerCase()}`)
                  .join(" · ")}
              </Cell>
            </Row>
            <Row>
              <Cell>Human queue</Cell>
              <Cell num>− {rupees(s.costs.handoff_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">
                <Link href="/queue" className="text-[var(--accent)] hover:underline">
                  {s.costs.n_handoff_cases} cases handed to a person
                </Link>
              </Cell>
            </Row>
            <Row>
              <Cell>
                <strong>Net recovered</strong>
              </Cell>
              <Cell num>
                <strong>{rupees(net.net_recovered_paise)}</strong>
              </Cell>
              <Cell className="text-[var(--text-3)]">
                {net.cost_per_100_recovered === null
                  ? "nothing recovered yet"
                  : `₹${net.cost_per_100_recovered} spent per ₹100 recovered`}
              </Cell>
            </Row>
          </Table>
          <Note>
            Gross recovery cannot go down by sending more messages, which is exactly what
            makes it the wrong headline. The modelled annoyance cost prices every decision
            before it is taken and is never booked here — only rupees that actually moved
            are.
          </Note>
        </Panel>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Recovery rate" value={pct(s.recovery_rate.rate)} sub={`${s.recovery_rate.numerator}/${s.recovery_rate.denominator} closed · strict ${pct(s.recovery_rate.strict_rate)}`} />
        <Stat label="Avg time to recovery" value={hours(s.avg_time_to_recovery_hours)} sub={s.time_basis} />
        <Stat
          label="Promises kept"
          value={`${s.promises.promises_kept}/${s.promises.n_promises}`}
          sub="dated promises the customer named on a call"
        />
        <Stat
          label="Audit chain"
          tone={s.audit_chain.status === "intact" ? "ok" : undefined}
          value={s.audit_chain.status}
          sub={`${s.audit_chain.n_entries} entries · ${s.audit_chain.n_unchained} unchained`}
        />
      </div>
    </>
  );
}
