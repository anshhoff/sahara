"use client";

/**
 * The page a recovery link opens.
 *
 * The update-link rung used to hand out a Razorpay-hosted Payment Link, which needed no
 * page of our own. It cannot any more: a test-mode account may create thirty Payment
 * Links for the life of the key and this one has spent all thirty, after which every
 * create returns `test mode limit of 30 reached for payment_link` and the executor
 * honestly falls back to a URL that goes nowhere. Orders have no such ceiling, so the
 * executor creates one of those instead (`_create_checkout_order`) and this page is the
 * hosted half Razorpay would otherwise have provided.
 *
 * Written for the customer, not the operator: no stage names, no rule IDs, no case
 * status. Someone arriving here has been sent a link about a failed payment, and the
 * only two things they need are what they owe and a way to pay it.
 *
 * Paying does NOT close the case from this page. Razorpay delivers `order.paid` to the
 * webhook receiver, `webhooks.extract()` reads `notes.case_id` off the payment, and the
 * case recovers through the same intake every other signal uses — so the page reports
 * the payment and then says plainly that it is waiting, rather than asserting an
 * outcome it is not the one to decide.
 */
import Script from "next/script";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { orderInfo, type OrderInfo } from "@/lib/demo";
import { rupees } from "@/lib/format";
import { Button, Empty } from "@/components/ui";
import { IconAlert } from "@/components/icons";

type Phase = "loading" | "ready" | "paying" | "paid" | "gone";

function Pay() {
  const orderId = useSearchParams().get("order_id");
  const [order, setOrder] = useState<OrderInfo | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [fault, setFault] = useState<string | null>(null);

  useEffect(() => {
    if (!orderId) {
      setPhase("gone");
      return;
    }
    orderInfo(orderId)
      .then((o) => {
        setOrder(o);
        // Razorpay marks an order `paid` once it is settled. Re-opening Checkout on one
        // would fail anyway, so the page says so instead of offering a button that cannot work.
        setPhase(o.status === "paid" ? "paid" : "ready");
      })
      .catch((e) => {
        setFault(e instanceof Error ? e.message : String(e));
        setPhase("gone");
      });
  }, [orderId]);

  const pay = useCallback(() => {
    if (!order || !window.Razorpay) {
      setFault("Razorpay Checkout did not load.");
      return;
    }
    setPhase("paying");
    const rzp = new window.Razorpay({
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      order_id: order.order_id,
      name: "Pro Plan",
      description: "Payment for your subscription",
      handler: () => setPhase("paid"),
      modal: { ondismiss: () => setPhase("ready") },
    });
    rzp.open();
  }, [order]);

  if (phase === "gone") {
    return (
      <Empty>
        {fault ?? "This link is missing its order reference."}
        <div className="mt-2 text-[12px]">
          Recovery links are created per payment attempt. Ask for a fresh one.
        </div>
      </Empty>
    );
  }

  if (phase === "loading" || !order) {
    return <p className="text-[13px] text-[var(--text-3)]">Loading your payment…</p>;
  }

  return (
    <div className="rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-1)]">
      <div className="eyebrow">Amount due</div>
      <div className="tnum mt-1 text-[32px] leading-none font-semibold tracking-[-0.02em]">
        {rupees(order.amount)}
      </div>
      <p className="mt-3 max-w-[46ch] text-[13.5px] leading-[1.7] text-[var(--text-2)]">
        Your last subscription payment didn&rsquo;t go through. Paying here settles it and
        keeps your plan active — nothing else changes.
      </p>

      {fault && (
        <div
          role="alert"
          className="mt-4 flex gap-2 rounded-[var(--r)] border border-[var(--danger-line)] bg-[var(--danger-soft)] px-3 py-2 text-[13px] text-[var(--danger)]"
        >
          <IconAlert size={15} className="mt-[2px] shrink-0" />
          <span className="min-w-0 break-words">{fault}</span>
        </div>
      )}

      {phase === "paid" ? (
        <div className="mt-5 rounded-[var(--r)] border border-[var(--ok-line)] bg-[var(--ok-soft)] px-4 py-3 text-[13px] leading-relaxed">
          <strong>Payment received.</strong>
          <div className="mt-1 text-[var(--text-2)]">
            Your subscription will show as active once your bank confirms — usually a few
            seconds.
          </div>
        </div>
      ) : (
        <div className="mt-5">
          <Button variant="primary" busy={phase === "paying"} onClick={pay}>
            Pay {rupees(order.amount)}
          </Button>
          <p className="mt-3 text-[12px] text-[var(--text-3)]">
            Test mode — use UPI <span className="mono">success@razorpay</span> to pay, or{" "}
            <span className="mono">failure@razorpay</span> to decline.
          </p>
        </div>
      )}
    </div>
  );
}

export default function PayPage() {
  return (
    <div className="mx-auto w-full max-w-[520px] px-4 py-10 md:px-6">
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="afterInteractive" />
      {/* useSearchParams needs a boundary or the whole route opts out of prerendering. */}
      <Suspense fallback={<p className="text-[13px] text-[var(--text-3)]">Loading…</p>}>
        <Pay />
      </Suspense>
    </div>
  );
}
