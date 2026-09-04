import { tryGet } from "@/lib/api";
import type { Summary } from "@/lib/types";
import { hours, pct, pp, rupees, words } from "@/lib/format";
import {
  Badge,
  Bar,
  Cell,
  Empty,
  Note,
  Panel,
  Row,
  SectionLabel,
  Stat,
  Table,
  TotalRow,
} from "@/components/ui";
import { Interval } from "@/components/CI";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";
import Link from "next/link";

export const dynamic = "force-dynamic";

/**
 * The overview.
 *
 * Reorganised around the fact that these numbers are not peers. One of them is the
 * claim; two of them qualify it; the rest describe the batch it was measured on. The
 * previous layout rendered all nine at 24px in three anonymous rows, which left the
 * reader to work out the ranking from the prose. Now the ranking is the layout: a hero
 * figure with its interval drawn under it, then restraint, then the arithmetic, then
 * the batch.
 */
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
  const netCi = net.net_incremental_paise_ci95;

  return (
    <>
      <PageHeader
        title="Overview"
        eyebrow="Recovery console"
        question="How much money came back because of the agent — not alongside it — and what did getting it cost?"
        actions={
          <>
            <Badge tone={s.audit_chain.status === "intact" ? "ok" : "danger"}>
              audit chain {s.audit_chain.status}
            </Badge>
            <span className="tnum text-[12px] text-[var(--text-3)]">
              {s.n_cases} cases · seed {s.seed ?? "—"}
            </span>
          </>
        }
      />

      <SectionLabel aside="a result is claimed only when its interval excludes zero">
        The headline
      </SectionLabel>

      {/* The hero is one surface split in two rather than two cards, because the
          figure and the evidence for it are one claim. The left half is what the
          project asserts; the right half is the arm comparison that licences the
          assertion, which is also what stops the card from being a big number
          floating in empty space. */}
      <div className="overflow-hidden rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-1)]">
        <div className="grid lg:grid-cols-[1.15fr_1fr]">
          <div className="p-5 lg:p-6">
            <div className="flex items-center gap-[6px]">
              <span
                aria-hidden="true"
                className={`h-[6px] w-[6px] rounded-full ${net.incremental_available ? "bg-[var(--ok)]" : "bg-[var(--neutral)]"}`}
              />
              <span className="eyebrow">Net incremental recovery · primary metric</span>
            </div>

            <div
              className={`tnum mt-2 text-[clamp(34px,6vw,var(--t-4xl))] leading-[1.03] font-semibold tracking-[-0.025em] ${
                net.incremental_available ? "text-[var(--ok)]" : ""
              }`}
            >
              {net.incremental_available ? rupees(net.net_incremental_paise) : "—"}
            </div>

            <p className="mt-3 max-w-[46ch] text-[13px] leading-[1.6] text-[var(--text-2)]">
              Money that came back <em>because of</em> the agent, minus every rupee it spent
              getting there. The one number this project stands behind.
            </p>

            {net.incremental_available && netCi ? (
              <div className="mt-4 max-w-[420px] border-t border-[var(--border)] pt-3">
                <Interval
                  value={net.net_incremental_paise ?? null}
                  ci={netCi}
                  significant={netCi[0] > 0 || netCi[1] < 0}
                  format={rupees}
                />
              </div>
            ) : (
              <p className="mt-4 border-t border-[var(--border)] pt-3 text-[12px] text-[var(--text-3)]">
                No control arm in this batch, so nothing here is incremental.
              </p>
            )}
          </div>

          <div className="border-t border-[var(--border)] bg-[var(--surface-2)] p-5 lg:border-t-0 lg:border-l lg:p-6">
            <div className="eyebrow">Where it comes from</div>
            {!inc.available ? (
              <p className="mt-3 text-[13px] text-[var(--text-2)]">{inc.reason}</p>
            ) : (
              <>
                <div className="mt-3 flex flex-col gap-[14px]">
                  {[
                    {
                      name: "treated",
                      tone: "ok" as const,
                      arm: inc.treated,
                      note: "intervened on",
                    },
                    {
                      name: "control",
                      tone: "control" as const,
                      arm: inc.control,
                      note: "held out, never contacted",
                    },
                  ].map((a) => (
                    <div key={a.name}>
                      <div className="flex items-baseline justify-between gap-3">
                        <Badge tone={a.tone}>{a.name}</Badge>
                        <span className="tnum text-[15px] font-semibold">{pct(a.arm.rate)}</span>
                      </div>
                      <div className="mt-[6px]">
                        <Bar
                          value={a.arm.rate}
                          tone={a.tone}
                          label={`${a.name} recovery rate ${pct(a.arm.rate)}`}
                        />
                      </div>
                      <div className="tnum mt-[5px] text-[11.5px] text-[var(--text-3)]">
                        {a.arm.recovered}/{a.arm.n} recovered · {a.note}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-4 border-t border-[var(--border)] pt-3">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="eyebrow">Lift</span>
                    <span className="tnum text-[15px] font-semibold">{pp(inc.lift)}</span>
                    <span className="tnum text-[11.5px] text-[var(--text-2)]">
                      95% CI [{pp(inc.lift_ci95[0])}, {pp(inc.lift_ci95[1])}]
                    </span>
                  </div>
                  <p className="mt-1 text-[11.5px] leading-snug text-[var(--text-3)]">
                    The secondary metric, and the more stable of the two —{" "}
                    <Link
                      href="/results"
                      className="text-[var(--accent)] underline decoration-[var(--accent-line)] underline-offset-2 hover:decoration-[var(--accent)]"
                    >
                      both reported in full
                    </Link>
                    .
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <SectionLabel aside="the harder claim">What it refused to do</SectionLabel>

      <div className="grid gap-3 sm:grid-cols-2">
        <Stat
          href="/results"
          label="Deliberately not contacted"
          value={s.declined_to_contact.n_declined}
          sub={`Cases the agent could have messaged and refused to — plus ${s.declined_to_contact.n_control_arm} more held out to measure the rest.`}
        />
        <Stat
          href="/fencing"
          label="Outreach to already-settled"
          value={s.fencing.outreach_to_settled}
          sub={`Of ${s.fencing.n_dispatches_fenced} dispatches fenced. A zero without a denominator attests to nothing.`}
        />
      </div>

      <SectionLabel>How the headline is built</SectionLabel>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Panel
          title="Treated vs control"
          aside="intention-to-treat"
          flush
          lede={
            <>
              Gross recovery cannot separate money the agent won from money that would
              have come back anyway. A randomised slice is held out — detected and
              diagnosed like any other case, then never intervened on — and the gap
              between the arms is the effect.
            </>
          }
          footer={
            inc.available ? (
              <Interval
                value={inc.lift}
                ci={inc.lift_ci95}
                significant={inc.significant}
                format={(x) => pp(x)}
              />
            ) : undefined
          }
        >
          {!inc.available ? (
            <div className="px-4 py-4">
              <Empty>{inc.reason}</Empty>
            </div>
          ) : (
            <>
              <Table
                head={[
                  "Arm",
                  { label: "n", num: true },
                  { label: "Recovered", num: true },
                  { label: "Rate", num: true, w: "22%" },
                  { label: "Organic", num: true },
                ]}
              >
                <Row>
                  <Cell>
                    <Badge tone="neutral">treated</Badge>
                  </Cell>
                  <Cell num>{inc.treated.n}</Cell>
                  <Cell num>{inc.treated.recovered}</Cell>
                  <Cell num>
                    <span className="flex items-center justify-end gap-2">
                      <Bar value={inc.treated.rate} tone="ok" label={`treated ${pct(inc.treated.rate)}`} />
                      <span className="w-[46px]">{pct(inc.treated.rate)}</span>
                    </span>
                  </Cell>
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
                  <Cell num>
                    <span className="flex items-center justify-end gap-2">
                      <Bar
                        value={inc.control.rate}
                        tone="control"
                        label={`control ${pct(inc.control.rate)}`}
                      />
                      <span className="w-[46px]">{pct(inc.control.rate)}</span>
                    </span>
                  </Cell>
                  <Cell num>
                    {inc.control.organic ?? "—"}{" "}
                    <span className="text-[var(--text-3)]">
                      {inc.control.organic_rate != null && `(${pct(inc.control.organic_rate)})`}
                    </span>
                  </Cell>
                </Row>
              </Table>
              <div className="px-4 pb-4">
                <Note>
                  The control arm is <strong>not silence.</strong> It reproduces
                  Razorpay&rsquo;s own retry schedule plus organic self-cure, so the lift is
                  measured against what the merchant would have got by changing nothing at
                  all. Organic counts are censored by treatment — a treated case that would
                  have self-cured on day nine often recovers on day two first — so the
                  balance evidence is the roll, not the outcome.
                </Note>
              </div>
            </>
          )}
        </Panel>

        <Panel
          title="Net of what it cost"
          aside={
            net.cost_per_100_recovered === null
              ? undefined
              : `₹${net.cost_per_100_recovered} per ₹100 recovered`
          }
          flush
        >
          <Table
            head={["", { label: "Amount", num: true }, { label: "What it is", w: "42%" }]}
          >
            <Row>
              <Cell>Gross recovered</Cell>
              <Cell num>{rupees(net.gross_recovered_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">every rupee that came back</Cell>
            </Row>
            <Row>
              <Cell>Outreach</Cell>
              <Cell num className="text-[var(--danger)]">− {rupees(s.costs.outreach_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">
                {Object.entries(s.executions_by_action)
                  .filter(([, n]) => n)
                  .map(([a, n]) => `${n} ${words(a).toLowerCase()}`)
                  .join(" · ")}
              </Cell>
            </Row>
            <Row>
              <Cell>Human queue</Cell>
              <Cell num className="text-[var(--danger)]">− {rupees(s.costs.handoff_paise)}</Cell>
              <Cell className="text-[var(--text-3)]">
                <Link
                  href="/queue"
                  className="text-[var(--accent)] underline decoration-[var(--accent-line)] underline-offset-2 hover:decoration-[var(--accent)]"
                >
                  {s.costs.n_handoff_cases} cases handed to a person
                </Link>
              </Cell>
            </Row>
            <TotalRow>
              <Cell>Net recovered</Cell>
              <Cell num>{rupees(net.net_recovered_paise)}</Cell>
              <Cell className="font-normal text-[var(--text-3)]">
                {net.cost_per_100_recovered === null
                  ? "nothing recovered yet"
                  : `₹${net.cost_per_100_recovered} spent per ₹100 recovered`}
              </Cell>
            </TotalRow>
          </Table>
          <div className="px-4 pb-4">
            <Note>
              Gross recovery cannot go down by sending more messages, which is exactly what
              makes it the wrong headline. The modelled annoyance cost prices every decision
              before it is taken and is never booked here — only rupees that actually moved
              are.
            </Note>
          </div>
        </Panel>
      </div>

      <SectionLabel aside={s.time_basis}>The batch it was measured on</SectionLabel>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          size="sm"
          href="/cases"
          label="Recovery rate"
          value={pct(s.recovery_rate.rate)}
          sub={`${s.recovery_rate.numerator}/${s.recovery_rate.denominator} closed · strict ${pct(s.recovery_rate.strict_rate)}`}
        />
        <Stat
          size="sm"
          label="Avg time to recovery"
          value={hours(s.avg_time_to_recovery_hours)}
          sub={s.time_basis}
        />
        <Stat
          size="sm"
          href="/promises"
          label="Promises kept"
          value={`${s.promises.promises_kept}/${s.promises.n_promises}`}
          sub="dated promises the customer named on a call"
        />
        <Stat
          size="sm"
          tone={s.audit_chain.status === "intact" ? "ok" : "danger"}
          label="Audit chain"
          value={s.audit_chain.status}
          sub={`${s.audit_chain.n_entries} entries · ${s.audit_chain.n_unchained} unchained`}
        />
      </div>
    </>
  );
}
