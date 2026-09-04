import { tryGet } from "@/lib/api";
import type { CategoryLift, Summary } from "@/lib/types";
import { pct, pp, rupees, words } from "@/lib/format";
import { Cell, Empty, Note, Panel, Row, Table, Unmeasured } from "@/components/ui";
import { Interval } from "@/components/CI";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";
import Link from "next/link";

export const dynamic = "force-dynamic";

type CategoryRow = {
  category: string;
  n_cases: number;
  n_recovered: number;
  recovery_rate: number;
  at_risk_paise: number;
  recovered_paise: number;
};

export default async function ByCause() {
  const [s, rows] = await Promise.all([
    tryGet<Summary>("/api/summary"),
    tryGet<CategoryRow[]>("/api/categories"),
  ]);
  if (!s || !rows) {
    return (
      <>
        <PageHeader title="By cause" />
        <ApiDown what="the per-category breakdown" />
      </>
    );
  }
  const lifts: CategoryLift[] = s.lift_by_category ?? [];

  return (
    <>
      <PageHeader
        title="By cause"
        question="Not every failure is equally recoverable — an expired card does not un-expire, and an empty account often refills by payday."
      />

      <div className="grid gap-4">
        <Panel
          title="Lift by cause"
          aside="including where the agent is flat"
          lede={
            <>
              A table in which every row is a win is a table nobody should believe.
              Publishing the causes where the agent does nothing is what makes the rest
              credible. Arm counts sit on every row: a lift over eight cases and a lift
              over eight hundred are the same number and not the same claim.
            </>
          }
        >
          {lifts.length === 0 ? (
            <Empty>No control arm in this batch, so there is no lift to split.</Empty>
          ) : (
            <Table head={["Cause", "Treated", "Control", "Lift, with its interval"]}>
              {lifts
                .filter((r) => r.treated.n || r.control.n)
                .map((r) => (
                  <Row key={r.category}>
                    <Cell>
                      <Link
                        href={`/cases?category=${r.category}`}
                        className="text-[var(--accent)] hover:underline"
                      >
                        {words(r.category)}
                      </Link>
                    </Cell>
                    <Cell num>
                      {r.treated.recovered}/{r.treated.n}
                    </Cell>
                    <Cell num className="text-[var(--control)]">
                      {r.control.recovered}/{r.control.n}
                    </Cell>
                    <Cell>
                      {r.lift === null ? (
                        <Unmeasured why={r.reason} />
                      ) : (
                        <Interval
                          value={r.lift}
                          ci={r.lift_ci95}
                          significant={r.significant}
                          format={(x) => pp(x)}
                        />
                      )}
                    </Cell>
                  </Row>
                ))}
            </Table>
          )}
          <Note>
            A cause with an empty arm reads <em>not measurable</em>, never <em>0.0pp</em>.
            Having nothing to say and having measured no effect are different claims, and
            a reader must be able to tell them apart.
          </Note>
        </Panel>

        <Panel title="Recovery by diagnosed cause" aside="gross, both arms combined">
          <Table head={["Cause", "Cases", "Recovered", "Rate", "₹ at risk", "₹ recovered"]}>
            {rows.map((r) => (
              <Row key={r.category}>
                <Cell>
                  <Link
                    href={`/cases?category=${r.category}`}
                    className="text-[var(--accent)] hover:underline"
                  >
                    {words(r.category)}
                  </Link>
                </Cell>
                <Cell num>{r.n_cases}</Cell>
                <Cell num>{r.n_recovered}</Cell>
                <Cell num>{pct(r.recovery_rate)}</Cell>
                <Cell num>{rupees(r.at_risk_paise)}</Cell>
                <Cell num>{rupees(r.recovered_paise)}</Cell>
              </Row>
            ))}
          </Table>
        </Panel>
      </div>
    </>
  );
}
