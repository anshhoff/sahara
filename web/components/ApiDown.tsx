import { API_BASE } from "@/lib/api";
import { IconAlert } from "./icons";

/**
 * What a panel shows when its endpoint did not answer.
 *
 * Deliberately NOT an empty table or a row of zeros: a dashboard that renders "0" when
 * it could not reach the database is worse than one that renders nothing, because the
 * zero is indistinguishable from a real measurement.
 *
 * It is also, deliberately, an error with a fix in it. An error state that names the
 * problem and not the command is a dead end, and the command is one line.
 */
export function ApiDown({ what }: { what: string }) {
  return (
    <div
      role="status"
      className="rounded-[var(--r-lg)] border border-[var(--warn-line)] bg-[var(--warn-soft)] p-5"
    >
      <div className="flex gap-3">
        <IconAlert size={18} className="mt-[2px] shrink-0 text-[var(--warn)]" />
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold text-[var(--text)]">Could not read {what}.</h2>
          <p className="mt-1 max-w-[70ch] text-[13px] leading-relaxed text-[var(--text-2)]">
            The API at <span className="mono">{API_BASE}</span> did not answer.
          </p>
          <pre className="mono mt-3 overflow-x-auto rounded-[var(--r-sm)] border border-[var(--warn-line)] bg-[var(--surface)] px-3 py-2 text-[12px] text-[var(--text)]">
            uvicorn app.main:app --reload
          </pre>
          <p className="mt-3 max-w-[70ch] border-t border-[var(--warn-line)] pt-3 text-[12px] leading-relaxed text-[var(--text-2)]">
            Nothing is rendered as zero here — a zero you cannot tell apart from a
            measurement is worse than a blank.
          </p>
        </div>
      </div>
    </div>
  );
}
