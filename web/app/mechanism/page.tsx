import { tryGet } from "@/lib/api";
import type { Mechanism } from "@/lib/types";
import { words } from "@/lib/format";
import { Badge, Cell, Note, Panel, Row, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ApiDown } from "@/components/ApiDown";

export const dynamic = "force-dynamic";

const KIND_TONE = {
  safety: "danger",
  contact_hygiene: "warn",
  transmission: "accent",
  economics: "neutral",
} as const;

export default async function MechanismPage() {
  const m = await tryGet<Mechanism>("/api/mechanism");
  if (!m) {
    return (
      <>
        <PageHeader title="How it works" />
        <ApiDown what="the mechanism" />
      </>
    );
  }

  const attempts = Number(m.bounds.max_attempts ?? 3);
  const cell = (category: string, attempt: number) =>
    m.policy.find((p) => p.category === category && p.attempt === attempt);

  return (
    <>
      <PageHeader
        title="How it works"
        question="The rules the agent chooses from, the ones it cannot cross, and how many times each actually fired in this batch."
      />

      <div className="grid gap-4">
        <Panel
          title="The guardrails"
          aside={`${m.invariants.length} rules, live counts`}
          lede={
            <>
              Every invariant is enforced in a module that imports neither the policy
              table nor the model, so a wrong policy cell cannot route around them and no
              model output can reach them. They run before a decision and again before
              execution. This panel is rendered from the API rather than from a copy of
              the rules — a gate described in one place and enforced in another eventually
              describes something the code no longer does.
            </>
          }
        >
          <div className="grid gap-3 md:grid-cols-2">
            {m.invariants.map((i) => (
              <div
                key={i.code}
                className="rounded-[var(--r)] border border-[var(--border)] bg-[var(--surface-2)] p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="mono rounded-[var(--r-sm)] bg-[var(--surface-3)] px-[6px] py-[2px] text-[12px] font-semibold">
                    {i.code}
                  </span>
                  <strong className="text-[13px]">{i.title}</strong>
                  <span className="grow" />
                  <Badge tone={KIND_TONE[i.kind as keyof typeof KIND_TONE] ?? "neutral"}>
                    {words(i.kind)}
                  </Badge>
                  <Badge tone={i.stops ? "danger" : "neutral"}>
                    {i.stops} stop{i.stops === 1 ? "" : "s"}
                  </Badge>
                  {i.defers > 0 && <Badge tone="warn">{i.defers} deferred</Badge>}
                </div>
                <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-2)]">{i.plain}</p>
                <p className="mono mt-2 border-t border-[var(--border)] pt-2 text-[11px] text-[var(--text-3)]">
                  {i.rule}
                </p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel
          title="The policy table"
          aside={`${m.categories.length} × ${attempts} = ${m.categories.length * attempts} cells, all filled`}
          lede="The intervention comes from a static table indexed by (failure cause, attempt number). It is total — every cell filled — so the lookup cannot fall through to a default, and every decision records the exact cell it came from."
        >
          <Table head={["Cause", ...Array.from({ length: attempts }, (_, i) => `Attempt ${i + 1}`)]}>
            {m.categories.map((category) => (
              <Row key={category}>
                <Cell>{words(category)}</Cell>
                {Array.from({ length: attempts }, (_, i) => {
                  const p = cell(category, i + 1);
                  return (
                    <Cell key={i}>
                      {p ? (
                        <>
                          <div className="font-medium">{words(p.action)}</div>
                          <div className="text-[11px] text-[var(--text-3)]">
                            {p.delay_hours ? `+${p.delay_hours} h` : "immediate"}
                          </div>
                        </>
                      ) : (
                        "—"
                      )}
                    </Cell>
                  );
                })}
              </Row>
            ))}
          </Table>
          <Note>
            Contact actions carry delay 0. The 24-hour spacing between two contacts is
            <em> not</em> a policy delay — it is invariant I2, enforced separately, which
            defers the execution. The spacing therefore holds even if this table is wrong.
          </Note>
        </Panel>

        <Panel title="The diagnosis rules" aside="checked first, in order; first hit wins">
          <Table head={["Rule", "Category", "Matched", "Patterns"]}>
            {m.rules.map((r) => (
              <Row key={r.id}>
                <Cell className="mono">{r.id}</Cell>
                <Cell>{words(r.category)}</Cell>
                <Cell num>{r.n_matched}</Cell>
                <Cell className="text-[var(--text-3)]">
                  {r.patterns.length ? (
                    <>
                      {r.patterns.join(" · ")}
                      {r.n_patterns > r.patterns.length && ` +${r.n_patterns - r.patterns.length} more`}
                    </>
                  ) : (
                    <em>fallback — nothing matched</em>
                  )}
                </Cell>
              </Row>
            ))}
          </Table>
          <Note>
            Order is part of the rule table&rsquo;s contract, not an accident of layout: an
            error string carrying two keywords resolves by table order, so inserting a rule
            in the wrong position silently reclassifies traffic.
          </Note>
        </Panel>
      </div>
    </>
  );
}
