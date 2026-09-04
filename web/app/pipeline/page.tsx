"use client";

/**
 * The ops view — `dashboard/live-pipeline.html`, brought across.
 *
 * This is the window the subscribe demo pops open beside itself: the same case the
 * customer is looking at, told in stage names and rule IDs instead of reassurance. It is
 * deliberately outside the console rail (see `components/Shell.tsx`) because it is a
 * companion panel to another window, not a place you navigate to.
 *
 * The stepper is the point. A reader watching a case for the first time needs to see
 * that DETECT → DIAGNOSE → DECIDE → EXECUTE actually happened in that order and that a
 * stop is a stop, and a scrolling log alone does not carry that.
 */
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  forceTick,
  latestScheduled,
  linkFrom,
  runStatus,
  type DemoRun,
  type Ref,
} from "@/lib/demo";
import { useDemoCase } from "@/components/useDemoCase";
import { rupees, when, words } from "@/lib/format";
import { Badge, Button, Empty, KeyValue, Note, Panel, type Tone } from "@/components/ui";
import { IconAlert, IconClock, IconExternal } from "@/components/icons";

const STAGES = ["detect", "diagnose", "decide", "execute", "outcome"] as const;

/** The node colour for a stage.
 *
 *  Written out rather than built by interpolation because Tailwind discovers classes by
 *  scanning source text: a name assembled at runtime is never generated. (Nor may this
 *  comment show the interpolated form as an example — the scanner reads comments too,
 *  and doing so emitted a rule with a brace in its selector and broke the CSS build.) */
const NODE: Record<Tone, string> = {
  ok: "bg-[var(--ok)]",
  warn: "bg-[var(--warn)]",
  danger: "bg-[var(--danger)]",
  control: "bg-[var(--control)]",
  accent: "bg-[var(--accent)]",
  neutral: "bg-[var(--neutral)]",
};

const STAGE_TONE: Record<string, Tone> = {
  detect: "neutral",
  diagnose: "control",
  decide: "warn",
  execute: "ok",
  compensate: "warn",
  stop: "danger",
  outcome: "ok",
};

function Stepper({ reached, stopped }: { reached: Set<string>; stopped: boolean }) {
  return (
    <ol className="flex flex-wrap gap-[6px]" aria-label="Pipeline stages reached">
      {STAGES.map((stage) => {
        const hit = reached.has(stage);
        const isStop = stage === "outcome" && stopped;
        return (
          <li
            key={stage}
            aria-current={hit ? "step" : undefined}
            className={`flex-1 basis-[92px] rounded-[var(--r-sm)] border px-2 py-[7px] text-center text-[11px] font-semibold tracking-[.04em] uppercase transition-colors duration-[var(--dur-slow)] ${
              !hit
                ? "border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-3)]"
                : isStop
                  ? "border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)]"
                  : "border-[var(--accent-line)] bg-[var(--accent-soft)] text-[var(--accent)]"
            }`}
          >
            {isStop ? "stopped" : stage}
          </li>
        );
      })}
    </ol>
  );
}

function Pipeline() {
  const params = useSearchParams();
  const demoId = params.get("demo_id");
  const caseId = params.get("case_id");
  const ref: Ref | null = caseId ? { case_id: caseId } : demoId ? { demo_id: demoId } : null;

  const { data, loading, error, refresh } = useDemoCase(ref);
  const [ticking, setTicking] = useState(false);
  // Only when the run actually opened this window. The narration belongs to the script,
  // not to the case, so a case opened any other way must not sprout someone else's beats.
  const [run, setRun] = useState<DemoRun | null>(null);
  const watchingRun = params.get("run") === "1";

  useEffect(() => {
    if (!watchingRun) return;
    let live = true;
    const read = () =>
      runStatus()
        .then((r) => live && setRun(r))
        .catch(() => {});   // a dropped poll is not worth interrupting a demo for
    void read();
    const timer = setInterval(read, 1000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [watchingRun]);

  if (!ref) {
    return (
      <Empty>
        No <span className="mono">demo_id</span> or <span className="mono">case_id</span> in the
        URL. Open this from{" "}
        <Link href="/demo" className="text-[var(--accent)] underline underline-offset-2">
          Test mode
        </Link>{" "}
        or from the subscribe demo.
      </Empty>
    );
  }

  const c = data?.case;
  const trail = data?.audit_trail ?? [];
  const decisions = data?.decisions ?? [];
  const reached = new Set(trail.map((e) => e.stage));
  // A stop is an outcome: the case reached its end, it just did not end in recovery.
  if (reached.has("stop")) reached.add("outcome");
  const stopped = Boolean(c?.status?.startsWith("stopped_"));
  const scheduled = latestScheduled(decisions);
  const link = linkFrom(decisions);
  const open = c?.status === "open";

  const runNow = async () => {
    setTicking(true);
    try {
      await forceTick(ref);
      await refresh();
    } finally {
      setTicking(false);
    }
  };

  return (
    <div className="grid gap-4">
      {watchingRun && run && run.status !== "idle" && (
        <Panel
          title="Scripted run"
          aside={
            run.status === "running"
              ? `${run.elapsed_seconds?.toFixed(0) ?? 0}s elapsed`
              : run.status
          }
          tone={run.status === "error" ? "danger" : undefined}
        >
          {run.error && (
            <div className="mb-3 flex gap-2 text-[13px] text-[var(--danger)]">
              <IconAlert size={15} className="mt-[2px] shrink-0" />
              <span className="min-w-0 break-words">{run.error}</span>
            </div>
          )}
          <ol className="flex flex-col gap-[6px]">
            {run.beats.map((b) => (
              <li key={b.n} className="flex items-baseline gap-2 text-[13px] leading-relaxed">
                <span className="tnum w-[46px] shrink-0 text-right text-[11.5px] text-[var(--text-3)]">
                  {b.elapsed_seconds.toFixed(1)}s
                </span>
                {/* A clock skip is called out rather than blended in: it is the one beat
                    that did not happen by itself, and a viewer is owed the distinction. */}
                <Badge tone={b.skipped_hours > 0 ? "warn" : "neutral"}>{b.label}</Badge>
                <span className="min-w-0 text-[var(--text-2)]">{b.detail}</span>
              </li>
            ))}
          </ol>
          <div className="mt-3">
            <Note>
              Each &ldquo;clock skipped&rdquo; stands in for real waiting the policy table
              and invariant I2 genuinely impose. Nothing in the case record was edited to
              get past them — the clock moved, and every gate was then evaluated by its own
              unmodified code against the later time.
            </Note>
          </div>
        </Panel>
      )}

      <Panel
        title="Case"
        aside={c ? `${trail.length} audit entries` : undefined}
        tone={stopped ? "danger" : c?.status === "recovered" ? "ok" : undefined}
      >
        {loading && !c ? (
          <p className="text-[13px] text-[var(--text-3)]">Waiting for the first webhook…</p>
        ) : !c ? (
          <Empty>
            {error ?? "No case has opened for this run yet."}
            <div className="mt-2 text-[12px]">
              A case exists only from a <span className="mono">payment.failed</span> onward — a
              payment that succeeds first time never opens one.
            </div>
          </Empty>
        ) : (
          <>
            <Stepper reached={reached} stopped={stopped} />

            <div className="mt-4">
              <KeyValue
                rows={[
                  {
                    k: "Case",
                    v: (
                      <Link
                        href={`/cases/${c.id}`}
                        className="mono text-[var(--accent)] underline decoration-[var(--accent-line)] underline-offset-2 hover:decoration-[var(--accent)]"
                      >
                        {c.id}
                      </Link>
                    ),
                  },
                  {
                    k: "Status",
                    v: (
                      <Badge
                        tone={
                          c.status === "recovered" ? "ok" : stopped ? "warn" : "accent"
                        }
                      >
                        {words(c.status)}
                      </Badge>
                    ),
                  },
                  { k: "Diagnosed cause", v: words(c.current_category) || "—" },
                  {
                    k: "Attempts",
                    v: `${c.attempt_count} / ${data?.bounds?.max_attempts ?? 3}`,
                  },
                  { k: "At risk", v: rupees(c.amount_at_risk_paise) },
                ]}
              />
            </div>

            {/* The scheduled-for line is the honest part of this demo: RETRY_LATER is
                genuinely hours out on the real wall clock, and the button says it is
                skipping that rather than pretending the wait did not exist. */}
            <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--border)] pt-3">
              <div className="min-w-0 grow text-[12.5px] leading-relaxed text-[var(--text-2)]">
                {!open ? (
                  "Case is closed — nothing left to run."
                ) : scheduled ? (
                  <>
                    <span className="mono">{words(scheduled.action)}</span> is scheduled for{" "}
                    <span className="tnum">{when(scheduled.scheduled_for)}</span> — real
                    wall-clock time, so it will not execute until then unless you run it now.
                  </>
                ) : (
                  "No decision currently scheduled."
                )}
              </div>
              <Button
                busy={ticking}
                disabled={!open || !scheduled || ticking}
                onClick={runNow}
                title="Pull this case's scheduled decision to now and run the same tick() the background loop runs"
              >
                <IconClock size={14} />
                Fast-forward to next action
              </Button>
            </div>

            {link && (
              <div
                className={`mt-4 rounded-[var(--r)] border p-3 text-[13px] ${
                  link.real
                    ? "border-[var(--accent-line)] bg-[var(--accent-soft)]"
                    : "border-[var(--warn-line)] bg-[var(--warn-soft)]"
                }`}
              >
                {link.real ? (
                  <>
                    <strong>
                      {link.surface === "checkout_order"
                        ? "Real Razorpay Order created."
                        : "Real Payment Link created."}
                    </strong>{" "}
                    Pay it to trigger recovery:{" "}
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mono inline-flex items-center gap-1 font-semibold break-all text-[var(--accent)] underline underline-offset-2"
                    >
                      {link.url}
                      <IconExternal size={12} />
                    </a>
                    {link.surface === "checkout_order" && (
                      <div className="mt-1 text-[12px] text-[var(--text-2)]">
                        An Order rather than a Payment Link, because a test-mode account
                        gets thirty Payment Links for the life of the key and this one has
                        spent all thirty. Same key, same test-mode rails, same{" "}
                        <span className="mono">notes.case_id</span> routing the payment
                        back to this case — only the hosted page is ours instead of
                        Razorpay&rsquo;s.
                      </div>
                    )}
                  </>
                ) : (
                  <div className="flex gap-2">
                    <IconAlert size={15} className="mt-[2px] shrink-0 text-[var(--warn)]" />
                    <div className="min-w-0">
                      <strong>Simulated Payment Link — not created for real.</strong>
                      <div className="mt-1 text-[var(--text-2)]">
                        Reason recorded by the executor:{" "}
                        <span className="mono">{link.reason}</span>
                      </div>
                      <div className="mono mt-1 break-all text-[12px] text-[var(--text-3)]">
                        {link.url}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel title="What it did" aside={open ? "live · polling every second" : "final"} flush>
        {trail.length === 0 ? (
          <div className="px-4 py-4">
            <Empty>Nothing has happened on this case yet.</Empty>
          </div>
        ) : (
          <ol className="px-4 py-3">
            {trail.map((e, i) => (
              <li key={e.seq} className="relative flex gap-3 pb-3 last:pb-0">
                <div className="relative w-[13px] shrink-0">
                  {i < trail.length - 1 && (
                    <span
                      aria-hidden="true"
                      className="absolute top-[16px] bottom-[-12px] left-1/2 w-px -translate-x-1/2 bg-[var(--border-2)]"
                    />
                  )}
                  <span
                    aria-hidden="true"
                    className={`absolute top-[7px] left-1/2 h-[8px] w-[8px] -translate-x-1/2 rounded-full ring-4 ring-[var(--surface)] ${NODE[STAGE_TONE[e.stage] ?? "neutral"]}`}
                  />
                </div>
                <div className="min-w-0 grow">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <Badge tone={STAGE_TONE[e.stage] ?? "neutral"}>{e.stage}</Badge>
                    <span className="text-[12px] text-[var(--text-3)]">by {e.actor}</span>
                    <span className="grow" />
                    <span className="tnum text-[11.5px] whitespace-nowrap text-[var(--text-3)]">
                      {when(e.created_at)}
                    </span>
                  </div>
                  <p className="mt-[4px] text-[13px] leading-relaxed">{e.summary}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
        <div className="px-4 pb-4">
          <Note>
            This is the same hash-chained trail the console shows — the full entry for every
            line, with its hash, is on{" "}
            {c ? (
              <Link
                href={`/cases/${c.id}`}
                className="text-[var(--accent)] underline underline-offset-2"
              >
                the case file
              </Link>
            ) : (
              "the case file"
            )}
            .
          </Note>
        </div>
      </Panel>
    </div>
  );
}

export default function PipelinePage() {
  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-6 md:px-6">
      <header className="mb-5">
        <div className="eyebrow">Sahara · ops view</div>
        <h1 className="mt-1 text-[20px] leading-tight font-semibold tracking-[-0.02em]">
          What Sahara is doing behind the scenes
        </h1>
        <p className="mt-2 max-w-[70ch] text-[13px] leading-relaxed text-[var(--text-2)]">
          The same case the customer is looking at, in stage names and rule IDs. Updates
          within about a second of each webhook.
        </p>
      </header>
      {/* useSearchParams needs a boundary or the whole route opts out of prerendering. */}
      <Suspense fallback={<p className="text-[13px] text-[var(--text-3)]">Loading…</p>}>
        <Pipeline />
      </Suspense>
    </div>
  );
}
