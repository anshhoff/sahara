import { API_BASE } from "@/lib/api";
import { Empty } from "./ui";

/**
 * What a panel shows when its endpoint did not answer.
 *
 * Deliberately NOT an empty table or a row of zeros: a dashboard that renders "0" when
 * it could not reach the database is worse than one that renders nothing, because the
 * zero is indistinguishable from a real measurement.
 */
export function ApiDown({ what }: { what: string }) {
  return (
    <Empty>
      <div className="font-medium text-[var(--text)]">Could not read {what}.</div>
      <div className="mt-1">
        The API at <span className="mono">{API_BASE}</span> did not answer. Start it with{" "}
        <span className="mono">uvicorn app.main:app --reload</span>.
      </div>
      <div className="mt-2 text-[12px]">
        Nothing is rendered as zero here — a zero you cannot tell apart from a
        measurement is worse than a blank.
      </div>
    </Empty>
  );
}
