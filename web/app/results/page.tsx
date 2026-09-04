import { tryGet } from "@/lib/api";
import type { Summary } from "@/lib/types";
import { pct, pp, rupees, words } from "@/lib/format";
import { Badge, Cell, Empty, Note, Panel, Row, Table } from "@/components/ui";
import { Interval } from "@/components/CI";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

export default async function Results() {
  const s = await tryGet<Summary>("/api/summary");
  if (!s) {
    return (
      <>
        <PageHeader title="Results" />
        <ApiDown what="the summary" />
      </>
    );
  }
  const cal = s.calibration;
  const lat = s.latency.by_stage;

  return (
    <>
      <PageHeader
        title="Results"
        question="One primary metric, one secondary, everything else descriptive — and a result is claimed only when its interval excludes zero."
      />

      <div className="grid gap-4">
        <Panel
          title="The headline"
          aside="docs/analysis-plan.md"
          lede={
            <>
              The primary metric is <strong>net incremental recovery</strong>: money that
              came back because of the agent, minus everything it spent getting there.
              The rate lift is the secondary and is reported alongside, never instead —
              quoting the rate while the money interval spans zero is the easy dishonesty
              here, and it is forbidden.
            </>
          }
        >
          {!s.net.incremental_available ? (
            <Empty>No control arm in this batch. Replay with a holdout to make this measurable.</Empty>
          ) : (
            <Table head={["Metric", "Estimate and interval", "Reads as"]}>
              <Row>
                <Cell>
                  <strong>Net incremental recovery</strong>
                  <div className="text-[12px] text-[var(--text-3)]">primary</div>
                </Cell>
                <Cell>
                  <Interval
                    value={s.net.net_incremental_paise ?? null}
                    ci={s.net.net_incremental_paise_ci95 ?? null}
                    significant={
                      s.net.net_incremental_paise_ci95
                        ? s.net.net_incremental_paise_ci95[0] > 0 ||
                          s.net.net_incremental_paise_ci95[1] < 0
                        : null
                    }
                    format={rupees}
                  />
                </Cell>
                <Cell className="text-[var(--text-3)]">
                  treated minus control, minus every rupee of outreach and human queue
                </Cell>
              </Row>
              <Row>
                <Cell>
                  <strong>Lift in recovery rate</strong>
                  <div className="text-[12px] text-[var(--text-3)]">secondary</div>
                </Cell>
                <Cell>
                  <Interval
                    value={s.incremental.lift}
                    ci={s.incremental.lift_ci95}
                    significant={s.incremental.significant}
                    format={(x) => pp(x)}
                  />
                </Cell>
                <Cell className="text-[var(--text-3)]">
                  the more stable of the two: ticket sizes span ₹199 to ₹4,999, so the
                  money estimate needs far more cases before it settles
                </Cell>
              </Row>
            </Table>
          )}
          <Note>
            The interval is <strong>sampling uncertainty only</strong>. It quantifies how
            much of the gap could be chance at this sample size, and says nothing about
            whether the underlying outcome model is right — on a synthetic batch that
            model is a stated assumption. What is measured here is the mechanism, not the
            market.
          </Note>
        </Panel>

        <Panel
          title="Are the agent's own priors any good?"
          aside={
            cal.available
              ? `Brier ${cal.brier_score.toFixed(4)} · ECE ${cal.ece.toFixed(4)} · ${cal.n_scored} scored`
              : undefined
          }
          lede={
            <>
              <code className="mono">P_RECOVER_PRIOR</code> decides which interventions fire
              and which cases go to a person. Until this table existed it had never been
              checked against a single realised outcome — and a number that decides where
              money goes and has never been scored is an assumption wearing a
              measurement&rsquo;s clothes.
            </>
          }
        >
          {!cal.available ? (
            <Empty>{cal.reason}</Empty>
          ) : (
            <>
              <Table head={["Cause / action", "n", "Prior said", "Realised", "Gap", ""]}>
                {cal.by_pair.map((p) => (
                  <Row key={`${p.category}/${p.action}`}>
                    <Cell>
                      {words(p.category)} <span className="text-[var(--text-3)]">/</span>{" "}
                      {words(p.action)}
                    </Cell>
                    <Cell num>{p.n}</Cell>
                    <Cell num>{p.prior.toFixed(3)}</Cell>
                    <Cell num>{p.realised.toFixed(3)}</Cell>
                    <Cell num>
                      {p.gap >= 0 ? "+" : ""}
                      {p.gap.toFixed(3)}
                    </Cell>
                    <Cell>
                      <Badge tone={p.direction === "optimistic" ? "warn" : "neutral"}>
                        {p.direction}
                      </Badge>
                    </Cell>
                  </Row>
                ))}
              </Table>
              <Note>{cal.attribution}</Note>
            </>
          )}
        </Panel>

        <Panel
          title="What the agent declined to do"
          aside={`${s.declined_to_contact.n_declined} cases · ${s.declined_to_contact.n_control_arm} more held out`}
          lede="What it refused is a harder claim than what it achieved, and it belongs in the headline rather than buried in a stop-status breakdown."
        >
          <Table head={["Cases", "Why the agent said no"]}>
            {s.declined_to_contact.by_reason
              .filter((r) => r.n && r.status !== "stopped_holdout")
              .map((r) => (
                <Row key={r.status}>
                  <Cell num>{r.n}</Cell>
                  <Cell>{r.why}</Cell>
                </Row>
              ))}
          </Table>
          <Note>{s.declined_to_contact.note}</Note>
        </Panel>

        <Panel title="Stage latency" aside="wall clock, milliseconds">
          {Object.keys(lat).length === 0 ? (
            <Empty>No timing recorded in this batch.</Empty>
          ) : (
            <Table head={["Stage", "n", "p50", "p95", "max"]}>
              {Object.entries(lat).map(([stage, v]) => (
                <Row key={stage}>
                  <Cell>{words(stage)}</Cell>
                  <Cell num>{v.n}</Cell>
                  <Cell num>{v.p50_ms.toFixed(2)}</Cell>
                  <Cell num>{v.p95_ms.toFixed(2)}</Cell>
                  <Cell num>{v.max_ms.toFixed(2)}</Cell>
                </Row>
              ))}
            </Table>
          )}
          <Note>{s.latency.basis}</Note>
        </Panel>
      </div>
    </>
  );
}
