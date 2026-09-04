"use client";

/**
 * The control room — the ONLY client component in this console (task 5.3).
 *
 * Everything else here is a read path with nothing to be interactive about, and
 * shipping those as client components would send JavaScript to draw a table. This one
 * polls a running job's output, which genuinely needs a browser.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";
import { Badge, Cell, Empty, Note, Panel, Row, Table } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";

type JobSnapshot = {
  id: string;
  kind: string;
  label: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  lines?: string[];
  next?: number;
  result?: unknown;
};

type ControlState = {
  enabled: boolean;
  db_path: string;
  llm_provider: string;
  razorpay_configured: boolean;
  live_link_budget: number;
  clock: { now: string; simulated: boolean };
  active_job: JobSnapshot | null;
  test_files: string[];
};

const TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  running: "warn",
  passed: "ok",
  succeeded: "ok",
  failed: "danger",
  error: "danger",
};

export default function ControlRoom() {
  const [state, setState] = useState<ControlState | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);

  const loadState = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/control/state`, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setState(await res.json());
      setError(null);
    } catch {
      setError("The control API did not answer. Start the FastAPI process, or it is switched off.");
    }
  }, []);

  useEffect(() => {
    void loadState();
  }, [loadState]);

  // Poll only while something is actually running. A dashboard that polls an idle
  // server forever is a dashboard that makes its own logs unreadable.
  useEffect(() => {
    if (!job || (job.status !== "running" && job.status !== "queued")) return;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/control/jobs/${job.id}?after=${cursor.current}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const snap: JobSnapshot = await res.json();
        if (snap.lines?.length) setLines((prev) => [...prev, ...snap.lines!]);
        if (typeof snap.next === "number") cursor.current = snap.next;
        setJob(snap);
        if (snap.status !== "running" && snap.status !== "queued") {
          setBusy(false);
          void loadState();
        }
      } catch {
        /* a dropped poll is not an error worth interrupting the operator for */
      }
    }, 900);
    return () => clearInterval(timer);
  }, [job, loadState]);

  const start = useCallback(async (path: string, body: unknown) => {
    setBusy(true);
    setLines([]);
    cursor.current = 0;
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      if (!res.ok) throw new Error(await res.text());
      setJob(await res.json());
      setError(null);
    } catch (e) {
      setBusy(false);
      setError(String(e));
    }
  }, []);

  const btn =
    "rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-3 py-[6px] text-[13px] font-medium hover:bg-[var(--surface-3)] disabled:opacity-50";

  return (
    <>
      <PageHeader
        title="Control room"
        question="Run the suite, replay the batch, advance the clock — the same commands the README gives, run verbatim."
      />

      {error && (
        <div className="mb-4 rounded-[var(--r)] border border-[var(--danger)] bg-[var(--danger-soft)] px-4 py-3 text-[13px] text-[var(--danger)]">
          {error}
        </div>
      )}

      <div className="grid gap-4">
        <Panel
          title="Run something"
          aside={state ? `db ${state.db_path}` : undefined}
          lede="These are subprocesses on a single-operator local app. The whole control API is switchable off in one place before this is ever bound to anything but localhost."
        >
          <div className="flex flex-wrap gap-2">
            <button
              className={btn}
              disabled={busy}
              onClick={() => start("/api/control/tests", {})}
            >
              Run the test suite
            </button>
            <button
              className={btn}
              disabled={busy}
              onClick={() => start("/api/control/batch", { n: 80, seed: 42, holdout: 0.35, live_links: 0 })}
            >
              Replay the batch (n=80, seed 42, holdout 0.35)
            </button>
            <button className={btn} disabled={busy} onClick={() => start("/api/control/tick", {})}>
              Advance one tick
            </button>
          </div>
          {state && (
            <Table head={["", ""]}>
              <Row>
                <Cell>LLM provider</Cell>
                <Cell className="mono">{state.llm_provider}</Cell>
              </Row>
              <Row>
                <Cell>Razorpay configured</Cell>
                <Cell>{state.razorpay_configured ? "yes" : "no"}</Cell>
              </Row>
              <Row>
                <Cell>Live link budget</Cell>
                <Cell num>{state.live_link_budget}</Cell>
              </Row>
              <Row>
                <Cell>Clock</Cell>
                <Cell className="mono">
                  {state.clock.now} {state.clock.simulated && <Badge tone="warn">simulated</Badge>}
                </Cell>
              </Row>
            </Table>
          )}
          <Note>
            Replaying the batch resets the database and regenerates the synthetic cases
            from the seed, so the numbers a judge sees here are the numbers they get from
            their own terminal.
          </Note>
        </Panel>

        <Panel
          title="Output"
          aside={
            job ? (
              <Badge tone={TONE[job.status] ?? "neutral"}>
                {job.status}
                {job.exit_code !== null && job.exit_code !== undefined ? ` · exit ${job.exit_code}` : ""}
              </Badge>
            ) : undefined
          }
        >
          {lines.length === 0 ? (
            <Empty>Nothing running. Start something above and the output streams here.</Empty>
          ) : (
            <pre className="mono max-h-[28rem] overflow-auto rounded-[var(--r-sm)] bg-[var(--surface-3)] p-3 text-[12px] whitespace-pre-wrap">
              {lines.join("\n")}
            </pre>
          )}
        </Panel>
      </div>
    </>
  );
}
