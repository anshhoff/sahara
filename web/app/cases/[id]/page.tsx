import { notFound } from "next/navigation";
import { API_BASE } from "@/lib/api";
import type { AuditEntry, CaseRecord } from "@/lib/types";
import { rupees, when, words, STATUS_TONE } from "@/lib/format";
import { Badge, Cell, Note, Panel, Row, Stat, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";

export const dynamic = "force-dynamic";

type Execution = Record<string, unknown> & { result_payload?: Record<string, unknown> | null };

type CaseFile = {
  case: CaseRecord;
  events: Record<string, unknown>[];
  diagnoses: Record<string, unknown>[];
  decisions: (Record<string, unknown> & { execution?: Execution | null })[];
  audit_trail: AuditEntry[];
  bounds: Record<string, number>;
};

const STAGE_TONE = {
  detect: "neutral",
  diagnose: "accent",
  decide: "accent",
  execute: "ok",
  compensate: "warn",
  stop: "danger",
  outcome: "ok",
} as const;

/**
 * The voice-and-promise case timeline (task 5.4).
 *
 * Fetched directly rather than through the typed client: `GetPath` only knows literal
 * OpenAPI paths, and `/api/cases/{case_id}` is a template. Worth naming rather than
 * hiding — the typed client covers the routes it can, and this is the edge of that.
 */
async function loadCase(id: string): Promise<CaseFile | null> {
  const res = await fetch(`${API_BASE}/api/cases/${encodeURIComponent(id)}`, {
    cache: "no-store",
    headers: { accept: "application/json" },
  }).catch(() => null);
  if (!res || !res.ok) return null;
  return (await res.json()) as CaseFile;
}

export default async function CaseDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const file = await loadCase(id);
  if (!file) notFound();

  const c = file.case;
  const voice = file.decisions
    .map((d) => d.execution?.result_payload)
    .find((p) => p && (p as Record<string, unknown>).inbound_reading) as
    | Record<string, unknown>
    | undefined;

  return (
    <>
      <PageHeader
        title={c.id}
        monoTitle
        eyebrow="Case file"
        crumbs={[{ href: "/cases", label: "Cases" }]}
        actions={
          <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{words(c.status)}</Badge>
        }
        question="The handoff artifact. This page is the complete case file a person picks up — nothing summarised away, nothing withheld."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Status"
          value={<Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{words(c.status)}</Badge>}
          sub={c.closed_at ? `closed ${when(c.closed_at)}` : "still open"}
        />
        <Stat
          label="₹ at risk"
          value={rupees(c.amount_at_risk_paise)}
          sub="one charge cycle, frozen at case creation — never projected forward"
        />
        <Stat
          label="Attempts"
          value={`${c.attempt_count} / ${file.bounds.max_attempts}`}
          sub="a stop and a handoff never consumed one"
        />
        <Stat
          label="Arm"
          tone={c.is_holdout ? "control" : undefined}
          value={c.is_holdout ? "control" : "treated"}
          sub={
            c.is_holdout
              ? "deliberately never intervened on"
              : `cause: ${words(c.current_category)}`
          }
        />
      </div>

      {voice && (
        <div className="mt-4">
          <Panel
            title="What the customer said"
            aside="inbound reading — no amount field, by design"
            lede={
              <>
                A closed-enum intent and an optional date are the only things that cross
                this boundary. There is no amount field and there cannot be one: a
                compromised model — or a caller who talks their way into one — has nowhere
                to put a rupee figure. The amount on this case comes from a signed webhook,
                never from anything anybody said out loud.
              </>
            }
          >
            <blockquote className="mb-3 border-l-2 border-[var(--accent-line)] bg-[var(--accent-soft)] px-3 py-2 text-[13px] italic">
              {String(voice.inbound_transcript ?? "— nobody answered —")}
            </blockquote>
            <Table head={["Field", "Value"]}>
              {Object.entries((voice.inbound_reading ?? {}) as Record<string, unknown>).map(
                ([k, v]) => (
                  <Row key={k}>
                    <Cell>{words(k)}</Cell>
                    <Cell className="mono">{String(v)}</Cell>
                  </Row>
                ),
              )}
            </Table>
            <Note>
              Language: <span className="mono">{String(voice.language ?? "—")}</span> ·{" "}
              {String(voice.transmission ?? "")}
            </Note>
          </Panel>
        </div>
      )}

      <div className="mt-4">
        <Panel
          title="Timeline"
          aside={`${file.audit_trail.length} entries, hash-chained`}
          lede="Append-only and gapless from 1. Every entry carries a SHA-256 over its own contents together with its predecessor's, so editing one word — or deleting one row — invalidates every hash after it."
        >
          {/* A rail, not a stack. Twelve identical bordered cards told the reader
              nothing about order; the chain is the point of this panel, so the entries
              hang off one continuous line with a stage-coloured node each, and the
              boxes go away. The line is drawn on the <li> rather than the <ol> so it
              stops at the last node instead of running past it. */}
          <ol className="grid">
            {file.audit_trail.map((e, i) => {
              const tone = STAGE_TONE[e.stage as keyof typeof STAGE_TONE] ?? "neutral";
              const last = i === file.audit_trail.length - 1;
              return (
                <li key={e.seq} className="relative flex gap-3 pb-4 last:pb-0">
                  {/* Both the line and the node are centred with left-1/2 rather than by
                      the flex container: an absolutely positioned child is placed at its
                      static position, which justify-center does not move. */}
                  <div className="relative w-[13px] shrink-0">
                    {!last && (
                      <span
                        aria-hidden="true"
                        className="absolute top-[16px] bottom-[-16px] left-1/2 w-px -translate-x-1/2 bg-[var(--border-2)]"
                      />
                    )}
                    <span
                      aria-hidden="true"
                      className={`absolute top-[6px] left-1/2 h-[9px] w-[9px] -translate-x-1/2 rounded-full ring-4 ring-[var(--surface)] ${
                        tone === "ok"
                          ? "bg-[var(--ok)]"
                          : tone === "warn"
                            ? "bg-[var(--warn)]"
                            : tone === "danger"
                              ? "bg-[var(--danger)]"
                              : tone === "accent"
                                ? "bg-[var(--accent)]"
                                : "bg-[var(--neutral)]"
                      }`}
                    />
                  </div>

                  <div className="min-w-0 grow">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="mono text-[11.5px] text-[var(--text-3)]">#{e.seq}</span>
                      <Badge tone={tone}>{e.stage}</Badge>
                      <span className="text-[12px] text-[var(--text-3)]">by {e.actor}</span>
                      <span className="grow" />
                      <span className="tnum text-[11.5px] whitespace-nowrap text-[var(--text-3)]">
                        {when(e.created_at)}
                      </span>
                    </div>
                    <p className="mt-[5px] text-[13px] leading-relaxed">{e.summary}</p>
                    <details className="mt-[6px]">
                      <summary className="w-fit text-[12px] text-[var(--text-3)] transition-colors hover:text-[var(--accent)]">
                        the full entry
                      </summary>
                      <pre className="mono mt-2 max-h-72 overflow-auto rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--surface-sunk)] p-2 text-[11px] leading-[1.55] whitespace-pre-wrap">
                        {JSON.stringify(e.detail, null, 2)}
                      </pre>
                      <div className="mono mt-1 text-[11px] break-all text-[var(--text-3)]">
                        {e.entry_hash ? `hash ${e.entry_hash.slice(0, 32)}…` : "unchained"}
                      </div>
                    </details>
                  </div>
                </li>
              );
            })}
          </ol>
        </Panel>
      </div>
    </>
  );
}
