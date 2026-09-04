"use client";

/**
 * Test mode — the `#/livedemo` view from the vanilla dashboard.
 *
 * The console's other ten pages read a batch that has already happened. This one runs the
 * pipeline live, against the running server, on a case that does not exist yet — which is
 * the only place a reader can watch DETECT → DIAGNOSE → DECIDE → EXECUTE happen rather
 * than read that it did.
 *
 * Four routes in. The first is the one to use in front of an audience; the rest descend
 * in convenience and ascend in realism:
 *   1. the scripted run — one click, ~90 seconds, the whole ladder to its stop
 *   2. the subscribe demo — a real Razorpay Order, told from the customer's side
 *   3. the raw checkout shortcut — the same Order, without the customer framing
 *   4. inject — skips Razorpay entirely, because test-mode checkout offers only a few
 *      fixed decline reasons and cannot reach all six diagnosed causes
 *
 * The scripted run is not a fourth kind of realism, it is route 4 driven by a timer
 * instead of by a hand: same intake(), same tick(), same gates. What it adds is that it
 * moves the clock rather than waiting out the policy table's 12-72h delays and I2's 24h
 * contact cooldown, which is the only reason a three-attempt case cannot otherwise be
 * shown in a sitting. See app/live_demo.py's scripted-run section.
 *
 * Inject is the one that needs its honesty stated plainly: it pushes the same shape of
 * `payment.failed` body through the identical `intake()` a live webhook calls, with
 * `source="synthetic"` the only difference. That is a real run of the pipeline. It is not
 * a real delivery from Razorpay, and the page says which is which.
 */
import Link from "next/link";
import Script from "next/script";
import { useEffect, useState } from "react";
import {
  createOrder,
  demoConfig,
  injectCategory,
  startRun,
  type DemoConfig,
} from "@/lib/demo";
import { PageHeader } from "@/components/PageHeader";
import { Badge, Button, Note, Panel, SectionLabel } from "@/components/ui";
import { IconAlert, IconBolt, IconExternal } from "@/components/icons";
import { words } from "@/lib/format";

/** The six the diagnoser can produce. `unknown` is included on purpose: refusing to
 *  guess is a behaviour worth being able to trigger on demand. */
const CATEGORIES = [
  "card_expired",
  "insufficient_funds",
  "issuer_declined",
  "authentication_failed",
  "invalid_payment_method",
  "unknown",
];

export default function TestMode() {
  const [cfg, setCfg] = useState<DemoConfig | null>(null);
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [amount, setAmount] = useState(499);
  const [busy, setBusy] = useState<null | "pay" | "inject" | "run">(null);
  // issuer_declined is the default because its policy row is the only one that uses
  // three DIFFERENT rungs — retry, then link, then voice — so one run shows the whole
  // ladder instead of the same action three times.
  const [runCategory, setRunCategory] = useState("issuer_declined");
  const [runOutcome, setRunOutcome] = useState("failed_again");
  const [message, setMessage] = useState<React.ReactNode>(null);
  const [fault, setFault] = useState<string | null>(null);

  useEffect(() => {
    demoConfig()
      .then(setCfg)
      .catch(() => setCfg({ configured: false, key_id: null, amount_paise: 49900 }));
  }, []);

  const openPipeline = (query: string) => {
    window.open(`/pipeline?${query}`, "sahara-ops");
  };

  const payNow = async () => {
    setBusy("pay");
    setFault(null);
    setMessage(null);
    try {
      const order = await createOrder();
      openPipeline(`demo_id=${encodeURIComponent(order.demo_id)}`);
      if (!window.Razorpay) throw new Error("Razorpay Checkout did not load.");
      const rzp = new window.Razorpay({
        key: order.key_id,
        amount: order.amount,
        currency: order.currency,
        order_id: order.order_id,
        name: "Sahara — Test mode",
        description: "Pro Plan — ₹499",
        notes: {
          case_source: "live-demo",
          subscription_id: order.demo_id,
          customer_id: order.demo_id,
        },
        modal: { ondismiss: () => setBusy(null) },
      });
      rzp.open();
      setMessage(
        <>
          Order <span className="mono">{order.order_id}</span> created. The ops view is
          watching it — fail the payment in checkout and it will update within a second.
        </>,
      );
    } catch (e) {
      setFault(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const runScript = async () => {
    setBusy("run");
    setFault(null);
    setMessage(null);
    try {
      const r = await startRun(runCategory, amount, runOutcome);
      if (r.case_id) openPipeline(`case_id=${encodeURIComponent(r.case_id)}&run=1`);
      setMessage(
        <>
          Running <strong>{words(runCategory)}</strong> end to end in the ops view. It
          narrates itself — no further clicks.
        </>,
      );
    } catch (e) {
      setFault(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const runInject = async () => {
    setBusy("inject");
    setFault(null);
    setMessage(null);
    try {
      const r = await injectCategory(category, amount);
      if (r.case_id) {
        openPipeline(`case_id=${encodeURIComponent(r.case_id)}`);
        setMessage(
          <>
            Opened{" "}
            <Link
              href={`/cases/${r.case_id}`}
              className="mono text-[var(--accent)] underline underline-offset-2"
            >
              {r.case_id}
            </Link>{" "}
            as <strong>{words(r.requested_category)}</strong>. The ops view is watching it.
          </>,
        );
      } else {
        setMessage(<>intake returned &ldquo;{r.status}&rdquo; and opened no case.</>);
      }
    } catch (e) {
      setFault(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="afterInteractive" />

      <PageHeader
        title="Test mode"
        eyebrow="Mechanism"
        question="A real Razorpay test-mode order, watched against the running server — not a replay. Fail it and Sahara's actual webhook receiver, diagnosis and decision run on it."
        actions={
          cfg ? (
            <Badge tone={cfg.configured ? "ok" : "warn"}>
              {cfg.configured ? "razorpay test mode configured" : "razorpay not configured"}
            </Badge>
          ) : undefined
        }
      />

      {fault && (
        <div
          role="alert"
          className="mb-4 flex gap-2 rounded-[var(--r)] border border-[var(--danger-line)] bg-[var(--danger-soft)] px-4 py-3 text-[13px] text-[var(--danger)]"
        >
          <IconAlert size={16} className="mt-[2px] shrink-0" />
          <span className="min-w-0 break-words">{fault}</span>
        </div>
      )}

      {message && (
        <div
          role="status"
          className="mb-4 rounded-[var(--r)] border border-[var(--accent-line)] bg-[var(--accent-soft)] px-4 py-3 text-[13px] leading-relaxed"
        >
          {message}
        </div>
      )}

      <SectionLabel aside="one click, about ninety seconds">Start here</SectionLabel>

      <div className="mb-4 overflow-hidden rounded-[var(--r-lg)] border border-[var(--accent-line)] bg-[var(--surface)] shadow-[var(--shadow-1)]">
        <div className="grid md:grid-cols-[1.3fr_1fr]">
          <div className="p-5">
            <h2 className="text-[15px] font-semibold tracking-tight">Run the whole story</h2>
            <p className="mt-2 max-w-[54ch] text-[13px] leading-[1.65] text-[var(--text-2)]">
              Opens a case and walks it through every rung of the ladder to whatever ends
              it, narrating each step in the ops view. Nothing here is a shortcut around
              the pipeline: the same <span className="mono">intake()</span> receives the
              failure, the same <span className="mono">tick()</span> executes each
              decision, and every gate is evaluated by its own code.
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="eyebrow">Failure cause</span>
                <select
                  value={runCategory}
                  onChange={(e) => setRunCategory(e.target.value)}
                  className="min-h-[36px] rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-2 py-[7px] text-[13px]"
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {words(c)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="eyebrow">Ends with</span>
                <select
                  value={runOutcome}
                  onChange={(e) => setRunOutcome(e.target.value)}
                  className="min-h-[36px] rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-2 py-[7px] text-[13px]"
                >
                  <option value="failed_again">Never recovers — agent stops</option>
                  <option value="recovered">Customer pays on the last rung</option>
                </select>
              </label>
              <Button
                variant="primary"
                busy={busy === "run"}
                disabled={busy !== null}
                onClick={runScript}
              >
                <IconBolt size={14} />
                Run it
              </Button>
            </div>
          </div>
          <div className="border-t border-[var(--border)] bg-[var(--surface-2)] p-5 md:border-t-0 md:border-l">
            <div className="eyebrow">What it skips, and what it doesn&rsquo;t</div>
            <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--text-2)]">
              The waits are real — 12 to 72 hours between silent retries, 24 hours between
              two contacts — and they are the product, not an obstacle. The run does not
              shorten them and does not edit the records that encode them. It moves the{" "}
              <span className="mono">clock</span>, so each gate is evaluated exactly as it
              would be tomorrow, and the ops view says how many hours each skip stood in
              for.
            </p>
          </div>
        </div>
      </div>

      <SectionLabel aside="when you want to drive it yourself">By hand</SectionLabel>

      <div className="overflow-hidden rounded-[var(--r-lg)] border border-[var(--accent-line)] bg-[var(--surface)] shadow-[var(--shadow-1)]">
        <div className="grid md:grid-cols-[1.3fr_1fr]">
          <div className="p-5">
            <h2 className="text-[15px] font-semibold tracking-tight">The subscribe demo</h2>
            <p className="mt-2 max-w-[54ch] text-[13px] leading-[1.65] text-[var(--text-2)]">
              A plain subscribe-and-pay page told from the customer&rsquo;s side — no stage
              names, no rule IDs. One real Razorpay failure seeds the case, and simple
              &ldquo;it worked&rdquo; / &ldquo;it didn&rsquo;t&rdquo; buttons stand in for
              whatever real-world signal Sahara would eventually get on its own, so you can
              walk a case through all three attempts without fighting checkout each time.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link
                href="/subscribe"
                target="_blank"
                className="inline-flex min-h-[36px] items-center gap-2 rounded-[var(--r-sm)] bg-[var(--accent)] px-[13px] py-[7px] text-[13px] font-medium text-[var(--on-accent)] transition-colors hover:bg-[var(--accent-2)]"
              >
                Open the subscribe demo
                <IconExternal size={13} />
              </Link>
              <Link
                href="/pipeline"
                target="sahara-ops"
                className="inline-flex min-h-[36px] items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-[13px] py-[7px] text-[13px] font-medium transition-colors hover:bg-[var(--surface-3)]"
              >
                Open the ops view
                <IconExternal size={13} />
              </Link>
            </div>
          </div>
          <div className="border-t border-[var(--border)] bg-[var(--surface-2)] p-5 md:border-t-0 md:border-l">
            <div className="eyebrow">How it goes</div>
            <ol className="mt-2 flex flex-col gap-2 text-[12.5px] leading-relaxed text-[var(--text-2)]">
              {[
                "Subscribe, then fail the payment in checkout — UPI failure@razorpay is the reliable one.",
                "Razorpay sends a real signed payment.failed. A case opens.",
                "The page tells the customer what is happening; the ops view tells you why.",
                "Walk it to attempt 3 and watch it stop and hand off rather than keep trying.",
              ].map((step, i) => (
                <li key={i} className="flex gap-2">
                  <span className="tnum mt-[1px] flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[10.5px] font-semibold text-[var(--accent)]">
                    {i + 1}
                  </span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>

      <SectionLabel aside="what the demo above is built on">Developer shortcuts</SectionLabel>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Panel
          title="Real checkout, no customer framing"
          lede={
            <>
              Creates the same real Order and opens the ops view on it. In checkout, fail the
              payment any way it lets you: netbanking&rsquo;s mock failure (&ldquo;declined by
              the bank&rdquo;) maps to a known rule; Wallet&rsquo;s generic &ldquo;temporary
              issue&rdquo; does not, and Sahara will honestly stop rather than guess at it.
            </>
          }
        >
          <Button variant="primary" busy={busy === "pay"} disabled={busy !== null} onClick={payNow}>
            <IconBolt size={14} />
            Pay ₹499
          </Button>
          <Note>
            The ops view&rsquo;s &ldquo;fast-forward&rdquo; button skips real wall-clock delays
            instead of waiting hours between attempts. It pulls the scheduled decision to now
            and runs the same <span className="mono">tick()</span> the background loop runs —
            nothing inside <span className="mono">execute_decision</span> is bypassed.
          </Note>
        </Panel>

        <Panel
          title="Inject a cause directly"
          lede={
            <>
              Razorpay&rsquo;s test-mode checkout only offers a few fixed decline reasons, so it
              cannot reliably reach every rule. This pushes the same shape of{" "}
              <span className="mono">payment.failed</span> body through{" "}
              <span className="mono">intake()</span> — the identical function a live webhook
              calls, with <span className="mono">source=&quot;synthetic&quot;</span> the only
              difference — so any of the six diagnosed causes can run the pipeline on demand.
            </>
          }
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="eyebrow">Failure cause</span>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="min-h-[36px] rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-2 py-[7px] text-[13px]"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {words(c)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="eyebrow">Amount (₹)</span>
              <input
                type="number"
                min={1}
                step={1}
                value={amount}
                onChange={(e) => setAmount(Math.max(1, Number(e.target.value) || 1))}
                className="tnum min-h-[36px] w-[104px] rounded-[var(--r-sm)] border border-[var(--border-2)] bg-[var(--surface)] px-2 py-[7px] text-[13px]"
              />
            </label>
            <Button busy={busy === "inject"} disabled={busy !== null} onClick={runInject}>
              Inject
            </Button>
          </div>
          <Note>
            Injected cases are marked <span className="mono">synthetic</span> at intake and can
            never be counted as live recovery. Choosing <strong>unknown</strong> is the
            interesting one: the agent refuses to act on a cause it cannot diagnose (I4) and
            hands the case straight to a person.
          </Note>
        </Panel>
      </div>
    </>
  );
}
