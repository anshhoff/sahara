"use client";

/**
 * Polls one demo case.
 *
 * Both live surfaces need the same loop — the customer page to know what to tell the
 * customer, the ops page to draw the trail — so it lives once. The vanilla pages each
 * carried their own copy with their own subtly different reset logic, which is how
 * `live-pipeline.html` ended up with a `resetForNewCase()` that `subscribe.html` had no
 * equivalent of.
 *
 * Two behaviours are deliberate and were preserved from those pages:
 *
 *   - Polling STOPS once the case is closed. A dashboard that keeps hitting an endpoint
 *     for a case that can no longer change is a dashboard that makes its own logs
 *     unreadable, and this one polls at 1s.
 *
 *   - A demo_id can outlive a case. A rapid second failure closes one case and opens a
 *     fresh one under the same demo_id, so the case id is watched and a change resets
 *     the trail — otherwise the new case's entries render against the old one's count
 *     and you get a mix of both.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { readCase, type DemoCase, type Ref } from "@/lib/demo";

const POLL_MS = 1000;

export type DemoState = {
  data: DemoCase | null;
  error: string | null;
  /** True until the first response lands, so the page can tell "empty" from "not yet". */
  loading: boolean;
  /** Re-read now, without waiting out the interval — used after every write. */
  refresh: () => Promise<void>;
};

export function useDemoCase(ref: Ref | null): DemoState {
  const [data, setData] = useState<DemoCase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(ref));
  const lastCaseId = useRef<string | null>(null);
  const key = ref ? JSON.stringify(ref) : null;

  const refresh = useCallback(async () => {
    if (!ref) return;
    try {
      const next = await readCase(ref);
      if (next.case?.id && next.case.id !== lastCaseId.current) {
        lastCaseId.current = next.case.id;
      }
      setData(next);
      setError(null);
    } catch (e) {
      // A dropped poll is not worth interrupting anyone for; the next tick retries. Only
      // the very first failure is surfaced, because that one means "nothing is there".
      setError((prev) => prev ?? (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
    // `ref` is an object literal at every call site and would change identity on every
    // render; `key` is its stable serialisation and is what this actually depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Nothing that is closed can change again, so the loop is not scheduled for one.
  // `closed` is a dependency of the effect rather than a flag checked inside it: that
  // way the interval is genuinely torn down by the cleanup on the render that closes
  // the case, instead of continuing to fire into a branch that returns early.
  const status = data?.case?.status;
  const closed = Boolean(status && status !== "open");

  useEffect(() => {
    if (!ref) {
      setLoading(false);
      return;
    }
    void refresh();
    if (closed) return;

    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, refresh, closed]);

  return { data, error, loading, refresh };
}
