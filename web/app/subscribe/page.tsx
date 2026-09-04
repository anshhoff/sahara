"use client";

/**
 * The subscribe demo — `dashboard/subscribe.html`, brought across.
 *
 * This is the one surface in the repo told from the CUSTOMER's side. No stage names, no
 * rule IDs, no rupee-at-risk: a person whose card just failed, being handled. That is the
 * whole reason it exists, and it is why it does not get the console rail — it is meant to
 * be indistinguishable from any other SaaS billing page until something goes wrong.
 *
 * The flow: a real Razorpay test-mode Order opens Checkout. Fail it (UPI `failure@razorpay`,
 * or netbanking's mock decline) and Razorpay sends a genuine signed `payment.failed`, which
 * opens a real case. Everything after that is the actual pipeline.
 *
 * The two "Simulate" buttons are not a shortcut around the agent — they stand in for the
 * next real-world SIGNAL the agent would eventually receive on its own (a customer paying a
 * link, a retry clearing), and they enter through the same `webhooks.intake()` a live
 * delivery does. Sahara never advances on a timer alone, so without them a demo would sit
 * there for hours. The copy says so rather than implying the case advanced by magic.
 */
import Link from "next/link";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createOrder,
  latestScheduled,
  linkFrom,
  simulate,
  forceTick,
  type Ref,
} from "@/lib/demo";
import { useDemoCase } from "@/components/useDemoCase";
import { IconCheck, IconExternal } from "@/components/icons";

type CardKind = "pending" | "warn" | "ok";

const CARD: Record<CardKind, string> = {
  pending: "border-[var(--border)] bg-[var(--surface-2)]",
  warn: "border-[var(--warn-line)] bg-[var(--warn-soft)]",
  ok: "border-[var(--ok-line)] bg-[var(--ok-soft)]",
};

function DemoButton({
  children,
  onClick,
  busy,
}: {
  children: React.ReactNode;
  onClick: () => void;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="min-h-[40px] grow basis-[150px] rounded-[10px] border border-[var(--border-2)] bg-[var(--surface)] px-3 py-[9px] text-[13px] font-semibold transition-colors hover:bg-[var(--surface-3)] disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

export default function SubscribePage() {
  const [demoId, setDemoId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [acting, setActing] = useState(false);
  const [firstTrySuccess, setFirstTrySuccess] = useState(false);
  const [fault, setFault] = useState<string | null>(null);
  const opsWindow = useRef<Window | null>(null);

  const ref: Ref | null = demoId ? { demo_id: demoId } : null;
  const { data, refresh } = useDemoCase(firstTrySuccess ? null : ref);

  const c = data?.case;
  const decisions = data?.decisions ?? [];
  const link = linkFrom(decisions);
  const scheduled = latestScheduled(decisions);
  const lastEntry = data?.audit_trail?.at(-1);

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActing(true);
      try {
        await fn();
        await refresh();
      } catch (e) {
        setFault(e instanceof Error ? e.message : String(e));
      } finally {
        setActing(false);
      }
    },
    [refresh],
  );

  // Keep the ops window pointed at this run.
  useEffect(() => {
    if (!demoId) return;
    const url = `/pipeline?demo_id=${encodeURIComponent(demoId)}`;
    opsWindow.current = window.open(url, "sahara-ops") ?? null;
  }, [demoId]);

  const startCheckout = async () => {
    setStarting(true);
    setFault(null);
    try {
      const order = await createOrder();
      setDemoId(order.demo_id);

      if (!window.Razorpay) {
        throw new Error("Razorpay Checkout did not load. Check the network and reload.");
      }
      const rzp = new window.Razorpay({
        key: order.key_id,
        amount: order.amount,
        currency: order.currency,
        order_id: order.order_id,
        name: "Sahara Cloud",
        description: "Pro Plan — ₹499/month",
        notes: {
          case_source: "live-demo",
          subscription_id: order.demo_id,
          customer_id: order.demo_id,
        },
        handler: () => {
          // A payment that succeeds first time never opens a case at all — cases exist
          // only from a payment.failed onward — so there is nothing to poll. Razorpay's
          // own success callback is the only signal there will ever be.
          setFirstTrySuccess(true);
        },
        modal: { ondismiss: () => setStarting(false) },
      });
      rzp.on("payment.failed", () => {
        /* the poll picks up the open case within a second */
      });
      rzp.open();
    } catch (e) {
      setFault(e instanceof Error ? e.message : String(e));
      setStarting(false);
    }
  };

  /* ---- what the customer is told ------------------------------------------------ */

  let kind: CardKind = "pending";
  let title = "Processing…";
  let body: React.ReactNode = "Setting things up.";
  let actions: React.ReactNode = null;
  let footnote: React.ReactNode = null;

  if (firstTrySuccess || c?.status === "recovered") {
    kind = "ok";
    title = "You're all set";
    body = firstTrySuccess
      ? "Your payment went through on the first try. Welcome to Sahara Cloud Pro."
      : "Your payment went through. Welcome to Sahara Cloud Pro.";
  } else if (c?.status === "stopped_unknown") {
    kind = "warn";
    title = "We couldn't tell what went wrong";
    body =
      "Your payment failed for a reason we could not identify with confidence. Rather than guess, we've flagged this for a person to follow up with you directly.";
  } else if (c?.status?.startsWith("stopped_")) {
    kind = "warn";
    title = "A specialist will reach out";
    body =
      "We weren't able to collect your payment automatically. We've stopped trying on our own and handed this to our support team.";
  } else if (c && scheduled) {
    // Deferred by a contact-hygiene invariant: the honest version of "not right now".
    const deferred = /deferred by (I2|I5|I7)/.test(lastEntry?.summary ?? "");
    kind = deferred ? "warn" : "pending";
    title = deferred ? "We'll reach out again soon" : "Payment failed — we'll retry automatically";
    body = deferred ? (
      <>
        <p className="mb-2">
          Your payment failed. Sahara doesn&rsquo;t contact customers outside 09:00–21:00 IST,
          so the next step is on hold until then.
        </p>
        <p className="text-[var(--text-2)]">{lastEntry?.summary}</p>
      </>
    ) : (
      "This happens sometimes. Sahara will quietly try charging your card again without bothering you."
    );
    actions = (
      <DemoButton busy={acting} onClick={() => act(() => forceTick(ref!))}>
        {deferred ? "Try now anyway" : "⏩ Time passes — run the retry now"}
      </DemoButton>
    );
    footnote = deferred
      ? "This will likely defer again until real time passes 09:00 IST — that's a genuine guardrail (invariant I5), not a bug."
      : "Sahara only acts again when a new signal arrives — a real retry outcome, or you telling it what happened. Nothing advances on a timer alone; this button stands in for that next real signal.";
  } else if (c && decisions.some((d) => d.status === "executed")) {
    kind = link ? "warn" : "pending";
    title = link ? "Update your payment method" : "Retrying…";
    body = link ? (
      <>
        <p className="mb-2">Your payment failed, so we sent you a link to update it.</p>
        <div className="rounded-[8px] border border-dashed border-[var(--accent-line)] bg-[var(--surface)] px-3 py-2 text-[12.5px] break-all">
          {link.real ? (
            <a
              href={link.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-semibold text-[var(--accent)] underline underline-offset-2"
            >
              {link.url}
              <IconExternal size={12} />
            </a>
          ) : (
            <span className="text-[var(--text-2)]">{link.url}</span>
          )}
        </div>
      </>
    ) : (
      "We just tried to charge your card again automatically."
    );
    actions = (
      <>
        <DemoButton busy={acting} onClick={() => act(() => simulate(ref!, "recovered"))}>
          {link ? "Simulate: I paid it" : "Simulate: it succeeded"}
        </DemoButton>
        <DemoButton busy={acting} onClick={() => act(() => simulate(ref!, "failed_again"))}>
          {link ? "Simulate: I ignored it" : "Simulate: it failed again"}
        </DemoButton>
      </>
    );
    footnote = (
      <>
        These two buttons stand in for the next real-world signal Sahara would eventually
        receive on its own — a customer paying (or ignoring) a link, or a retry actually
        clearing.
        {link && !link.real && (
          <> That link is a stand-in, not a payable Razorpay Payment Link — the ops view shows why.</>
        )}
      </>
    );
  } else if (c) {
    title = "Processing…";
    body = "Working on it.";
  }

  const started = Boolean(demoId) || firstTrySuccess;

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="afterInteractive" />

      <div className="flex min-h-dvh justify-center px-5 py-12">
        <div className="w-full max-w-[430px]">
          <div className="mb-6 flex items-center justify-center gap-2">
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
              <rect width="24" height="24" rx="7" fill="var(--accent)" />
              <path
                d="M7 14.5c1.6-5.2 3.4-5.2 5 0 1.6 5.2 3.4 5.2 5 0"
                fill="none"
                stroke="#fff"
                strokeWidth="1.9"
                strokeLinecap="round"
              />
            </svg>
            <span className="text-[15px] font-bold tracking-tight">Sahara Cloud</span>
          </div>

          <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface)] p-7 shadow-[var(--shadow-2)]">
            <p className="text-[19px] font-bold tracking-tight">Pro Plan</p>
            <p className="mt-1 text-[13px] text-[var(--text-2)]">
              Everything you need to ship, monitored around the clock.
            </p>
            <p className="mt-4 text-[32px] leading-none font-extrabold tracking-[-0.02em]">
              ₹499{" "}
              <span className="text-[15px] font-medium text-[var(--text-2)]">/ month</span>
            </p>
            <ul className="mt-5 mb-6 flex flex-col gap-[6px]">
              {["Unlimited projects", "Priority support", "Advanced analytics"].map((f) => (
                <li key={f} className="flex items-center gap-2 text-[13px] text-[var(--text-2)]">
                  <IconCheck size={14} className="shrink-0 text-[var(--accent)]" />
                  {f}
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={startCheckout}
              disabled={starting || started}
              className="min-h-[46px] w-full rounded-[10px] bg-[var(--accent)] px-4 text-[15px] font-semibold text-[var(--on-accent)] transition-colors hover:bg-[var(--accent-2)] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {starting && !started ? "Opening checkout…" : started ? "Subscribed" : "Subscribe"}
            </button>
          </div>

          {fault && (
            <div
              role="alert"
              className="mt-4 rounded-[12px] border border-[var(--danger-line)] bg-[var(--danger-soft)] p-4 text-[13px] text-[var(--danger)]"
            >
              {fault}
            </div>
          )}

          {started && (
            <div className={`mt-4 rounded-[14px] border p-5 ${CARD[kind]}`} aria-live="polite">
              <p className="text-[15px] font-bold">{title}</p>
              <div className="mt-[6px] text-[13px] leading-relaxed text-[var(--text)]">{body}</div>
              {actions && <div className="mt-4 flex flex-wrap gap-2">{actions}</div>}
              {footnote && (
                <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-2)]">{footnote}</p>
              )}
            </div>
          )}

          <p className="mt-5 text-center text-[12px]">
            <Link
              href={demoId ? `/pipeline?demo_id=${encodeURIComponent(demoId)}` : "/pipeline"}
              target="sahara-ops"
              className="text-[var(--text-3)] underline underline-offset-2 hover:text-[var(--accent)]"
            >
              See what&rsquo;s happening behind the scenes →
            </Link>
          </p>
        </div>
      </div>
    </>
  );
}
