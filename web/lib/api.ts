/**
 * The typed API client (task 5.2).
 *
 * `GetPath` is derived from `paths` in `schema.d.ts`, which is generated from the
 * FastAPI OpenAPI document by `scripts/dump_openapi.py` + `openapi-typescript`. So a
 * renamed route stops the frontend BUILD rather than the page — which is the whole
 * point of the step. The API was already the contract; this makes the contract
 * machine-checkable instead of a convention two codebases agree to remember.
 *
 * What it does NOT give us is deep response shapes: every dashboard route is annotated
 * `-> dict[str, Any]` in Python, so OpenAPI describes each body as an open object. The
 * domain types in `types.ts` are therefore hand-written and are asserted, not proven.
 * Saying so is more useful than implying a guarantee that is not there — and the
 * routes, the query parameters and the method are genuinely checked.
 */
import type { paths } from "./schema";

export type GetPath = {
  [P in keyof paths]: paths[P] extends { get: unknown } ? P : never;
}[keyof paths];

/**
 * Where the FastAPI process lives. Server components fetch it directly; there is no
 * proxy layer, because adding one would put a second thing between the numbers and the
 * database that could disagree with either.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? process.env.API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(readonly path: string, readonly status: number, readonly body: string) {
    super(`${path} → ${status}`);
    this.name = "ApiError";
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/**
 * One GET. `no-store` on every read: this dashboard exists to show what the database
 * says right now, and a cached headline number that disagrees with the database is the
 * exact failure mode the whole project is built against.
 */
export async function get<T>(path: GetPath, query?: Query): Promise<T> {
  const url = `${API_BASE}${withQuery(path as string, query)}`;
  const res = await fetch(url, { cache: "no-store", headers: { accept: "application/json" } });
  if (!res.ok) throw new ApiError(path as string, res.status, await res.text().catch(() => ""));
  return (await res.json()) as T;
}

/**
 * A read that is allowed to fail. Every page is built from several independent
 * endpoints, and one of them being unavailable should cost that panel, not the page —
 * a results page that 500s because the latency endpoint is unhappy is worse than a
 * results page with one empty panel and a reason in it.
 */
export async function tryGet<T>(path: GetPath, query?: Query): Promise<T | null> {
  try {
    return await get<T>(path, query);
  } catch {
    return null;
  }
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new ApiError(path, res.status, await res.text().catch(() => ""));
  return (await res.json()) as T;
}
