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
import { Badge, Button, Empty, KeyValue, Note, Panel } from "@/components/ui";
import { IconAlert } from "@/components/icons";
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
  result?: { upload_id?: string | null; cases_file?: string | null; summary?: unknown } | null;
};

type ControlState = {
  enabled: boolean;
  db_path: string;
  llm_provider: string;
  razorpay_configured: boolean;
  live_link_budget: number;
  clock: { now: string; simulated: boolean };
  active_job: JobSnapshot | null;
  categories: string[];
  test_files: string[];
};

type UploadedCases = { uploadId: string; casesFile: string; filename: string; nCases: number; rowErrors: string[] };

/** Pending destructive action, held until the inline confirmation fires. Both branches
 *  reset the database the same way `run_batch.py` always has — this is not two
 *  features with different risk, it is one risk with two sources for the cases. */
type PendingReplay = { kind: "generate" } | { kind: "csv"; uploadId: string };

const TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  running: "warn",
  passed: "ok",
  succeeded: "ok",
  failed: "danger",
  error: "danger",
};

// A small operator-facing field, not a design-system component: this page is the
// console's one client component and the only place a labelled number input belongs.
function NumberField({
  label, value, onChange, min, max, step = 1, width = 76,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="eyebrow">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        style={{ width }}
        onChange={(e) => onChange(Number(e.target.value))}
        className="tnum min-h-[34px] rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-2 py-[6px] text-[13px]"
      />
    </label>
  );
}

export default function ControlRoom() {
  const [state, setState] = useState<ControlState | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingReplay | null>(null);
  const cursor = useRef(0);

  // Batch parameters. One `seed` field governs both: for a generated batch it drives
  // `generate_synthetic.py --seed`; for an uploaded CSV it only fills in whatever the
  // CSV itself left blank (amount, timing, error flavour — see csv_to_cases.py) and
  // the outcome model's own random rolls. Either way it is the number a re-run needs
  // to reproduce the exact same batch.
  const [n, setN] = useState(80);
  const [seed, setSeed] = useState(42);
  const [holdout, setHoldout] = useState(0.35);
  const [liveLinks, setLiveLinks] = useState(0);

  // CSV upload
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<UploadedCases | null>(null);

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
          setUploading(false);
          void loadState();
          if (snap.kind === "cases_upload") {
            if (snap.status === "passed" && snap.result?.upload_id && snap.result?.cases_file) {
              setUploaded({
                uploadId: snap.result.upload_id,
                casesFile: snap.result.cases_file,
                filename: csvFile?.name ?? "uploaded.csv",
                nCases: Number((snap.lines ?? []).find((l) => l.startsWith("wrote "))
                  ?.match(/: (\d+) cases/)?.[1] ?? 0),
                rowErrors: (snap.lines ?? [])
                  .filter((l) => l.trim().startsWith("!"))
                  .map((l) => l.trim().replace(/^!\s*/, "")),
              });
            } else {
              setUploadError("Conversion failed — see the output below.");
            }
          }
        }
      } catch {
        /* a dropped poll is not an error worth interrupting the operator for */
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const uploadCsv = useCallback(async () => {
    if (!csvFile) return;
    setUploading(true);
    setUploadError(null);
    setUploaded(null);
    try {
      const text = await csvFile.text();
      await start("/api/control/upload-cases", {
        csv_content: text, filename: csvFile.name, seed,
      });
    } catch (e) {
      setUploading(false);
      setUploadError(e instanceof Error ? e.message : String(e));
    }
  }, [csvFile, seed, start]);

  const runPending = useCallback(() => {
    if (!pending) return;
    setPending(null);
    if (pending.kind === "generate") {
      void start("/api/control/batch", { n, seed, holdout, live_links: liveLinks });
    } else {
      void start("/api/control/batch", {
        seed, holdout, live_links: liveLinks, cases_upload_id: pending.uploadId,
      });
    }
  }, [pending, n, seed, holdout, liveLinks, start]);

  return (
    <>
      <PageHeader
        title="Control room"
        eyebrow="Records"
        question="Run the suite, replay the batch, advance the clock — the same commands the README gives, run verbatim."
        actions={
          state ? (
            <Badge tone={state.clock.simulated ? "warn" : "neutral"}>
              clock {state.clock.simulated ? "simulated" : "real"}
            </Badge>
          ) : undefined
        }
      />

      {error && (
        <div
          role="alert"
          className="mb-4 flex gap-2 rounded-[var(--r)] border border-[var(--danger-line)] bg-[var(--danger-soft)] px-4 py-3 text-[13px] text-[var(--danger)]"
        >
          <IconAlert size={16} className="mt-[2px] shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}

      <div className="grid gap-4">
        <Panel
          title="Run something"
          aside={state ? `db ${state.db_path}` : undefined}
          lede="These are subprocesses on a single-operator local app. The whole control API is switchable off in one place before this is ever bound to anything but localhost."
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              busy={busy && job?.kind === "tests"}
              disabled={busy}
              onClick={() => start("/api/control/tests", {})}
            >
              Run the test suite
            </Button>
            <Button disabled={busy} onClick={() => start("/api/control/tick", {})}>
              Advance one tick
            </Button>
          </div>

          {/* Batch parameters — shared by both replay sources below. Editable rather
              than fixed at n=80/seed=42, so a run can be repeated at a different scale
              or reproduced from a different seed without dropping to a terminal. */}
          <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-[var(--border)] pt-3">
            <NumberField label="n (generated only)" value={n} onChange={setN} min={60} max={400} />
            <NumberField label="seed" value={seed} onChange={setSeed} min={0} width={88} />
            <NumberField label="holdout" value={holdout} onChange={setHoldout} min={0} max={0.6} step={0.05} width={72} />
            <NumberField label="live links" value={liveLinks} onChange={setLiveLinks} min={0} max={5} width={64} />
          </div>

          {/* The destructive one is separated from the other two and asks first. It
              drops the database and regenerates every case from the seed; a control
              that does that must not be the same grey rectangle as "advance one
              tick", and it must not fire on a single stray click. The confirmation is
              inline rather than window.confirm, which blocks the page and cannot be
              styled or dismissed with Escape. */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {pending?.kind !== "generate" ? (
              <Button variant="danger" disabled={busy} onClick={() => setPending({ kind: "generate" })}>
                Replay the batch…
              </Button>
            ) : (
              <span className="inline-flex flex-wrap items-center gap-2 rounded-[var(--r-sm)] border border-[var(--danger-line)] bg-[var(--danger-soft)] px-2 py-[5px]">
                <span className="text-[12.5px] text-[var(--danger)]">
                  This drops the database and regenerates {n} cases from seed {seed}.
                </span>
                <Button variant="danger" busy={busy && job?.kind === "batch"} disabled={busy} onClick={runPending}>
                  Confirm replay
                </Button>
                <Button onClick={() => setPending(null)}>Cancel</Button>
              </span>
            )}
          </div>

          {state && (
            <div className="mt-4">
              <KeyValue
                rows={[
                  { k: "LLM provider", v: <span className="mono">{state.llm_provider}</span> },
                  {
                    k: "Razorpay configured",
                    v: (
                      <Badge tone={state.razorpay_configured ? "ok" : "neutral"}>
                        {state.razorpay_configured ? "yes" : "no"}
                      </Badge>
                    ),
                  },
                  { k: "Live link budget", v: state.live_link_budget },
                  {
                    k: "Clock",
                    v: (
                      <span className="inline-flex flex-wrap items-center gap-2">
                        <span className="mono">{state.clock.now}</span>
                        {state.clock.simulated && <Badge tone="warn">simulated</Badge>}
                      </span>
                    ),
                  },
                ]}
              />
            </div>
          )}
          <Note>
            Replaying the batch resets the database and regenerates the synthetic cases
            from the seed, so the numbers a judge sees here are the numbers they get from
            their own terminal.
          </Note>
        </Panel>

        <Panel
          title="Bring your own cases"
          aside={state ? `${state.categories.length} categories` : undefined}
          lede={
            <>
              Upload a CSV instead of generating synthetic cases. Same replay underneath —{" "}
              <span className="mono">run_batch.py --cases</span> — just fed a different
              file. Required column: <span className="mono">category</span>. Optional:{" "}
              <span className="mono">category_truth</span>, <span className="mono">amount_rupees</span>,{" "}
              <span className="mono">outcome_script</span>, <span className="mono">opted_out</span>,{" "}
              <span className="mono">occurred_hours_ago</span>, <span className="mono">error_description</span>.
              Anything left blank is sampled the same way the generator samples it — see{" "}
              <span className="mono">scripts/csv_to_cases.py</span> for the exact rules.
            </>
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex min-h-[36px] cursor-pointer items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-[13px] py-[7px] text-[13px] font-medium transition-colors hover:bg-[var(--surface-3)]">
              {csvFile ? csvFile.name : "Choose CSV…"}
              <input
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(e) => {
                  setCsvFile(e.target.files?.[0] ?? null);
                  setUploaded(null);
                  setUploadError(null);
                }}
              />
            </label>
            <Button
              variant="primary"
              busy={uploading}
              disabled={!csvFile || uploading || busy}
              onClick={uploadCsv}
            >
              Convert &amp; validate
            </Button>
            {uploaded && (
              <Badge tone="ok">
                {uploaded.nCases} case{uploaded.nCases === 1 ? "" : "s"} ready
              </Badge>
            )}
          </div>

          {uploadError && (
            <p className="mt-3 text-[13px] text-[var(--danger)]">{uploadError}</p>
          )}

          {uploaded && (
            <div className="mt-3 rounded-[var(--r)] border border-[var(--ok-line)] bg-[var(--ok-soft)] px-3 py-2">
              <p className="text-[13px] leading-relaxed">
                <span className="mono">{uploaded.filename}</span> converted to{" "}
                <strong>{uploaded.nCases}</strong> case{uploaded.nCases === 1 ? "" : "s"}
                {uploaded.rowErrors.length > 0 && (
                  <> ({uploaded.rowErrors.length} row{uploaded.rowErrors.length === 1 ? "" : "s"} skipped)</>
                )}
                . Uses the seed above ({seed}) for anything the CSV left blank.
              </p>
              {uploaded.rowErrors.length > 0 && (
                <ul className="mono mt-2 flex flex-col gap-[2px] text-[11.5px] text-[var(--text-2)]">
                  {uploaded.rowErrors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              )}
              {uploaded.nCases < 60 && (
                <p className="mt-2 text-[12px] text-[var(--text-2)]">
                  Fewer than 60 cases: some acceptance checks (e.g. the arm-balance
                  self-cure check) compare rates between arms and are undefined at this
                  scale — the same reason the generator refuses n below 60. The batch
                  still replays correctly; only that one statistical check may report
                  &ldquo;FAIL&rdquo; on a very small file.
                </p>
              )}

              {pending?.kind === "csv" && pending.uploadId === uploaded.uploadId ? (
                <span className="mt-3 inline-flex flex-wrap items-center gap-2 rounded-[var(--r-sm)] border border-[var(--danger-line)] bg-[var(--danger-soft)] px-2 py-[5px]">
                  <span className="text-[12.5px] text-[var(--danger)]">
                    This drops the database and replaces it with these {uploaded.nCases} cases.
                  </span>
                  <Button variant="danger" busy={busy && job?.kind === "batch"} disabled={busy} onClick={runPending}>
                    Confirm replay
                  </Button>
                  <Button onClick={() => setPending(null)}>Cancel</Button>
                </span>
              ) : (
                <div className="mt-3">
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => setPending({ kind: "csv", uploadId: uploaded.uploadId })}
                  >
                    Replay this batch…
                  </Button>
                </div>
              )}
            </div>
          )}
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
            <pre
              aria-live="polite"
              aria-label="Job output"
              className="mono max-h-[28rem] overflow-auto rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--surface-sunk)] p-3 text-[12px] leading-[1.55] whitespace-pre-wrap"
            >
              {lines.join("\n")}
            </pre>
          )}
        </Panel>
      </div>
    </>
  );
}
