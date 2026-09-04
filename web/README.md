# web — the Next.js console

App Router, TypeScript, Tailwind v4. Reads the FastAPI backend; the only writes are the
control room and the live-demo routes.

```bash
uvicorn app.main:app --reload      # terminal 1 — the API on :8000
cd web && npm run dev              # terminal 2 — the console on :3000
```

`NEXT_PUBLIC_API_BASE` overrides the API origin (default `http://127.0.0.1:8000`).

## Server components for every read

Every read path is a server component. The console's job is to show what the database
says, and shipping a client bundle to draw a table of numbers that never change after
render is work for nothing.

The `"use client"` files are the ones that cannot be anything else:

| File | Why it needs a browser |
|---|---|
| `components/Nav.tsx` | reads the current route for the active state, and owns the mobile drawer |
| `components/Shell.tsx` | decides which routes get the console rail (see below) |
| `app/control/page.tsx` | polls a running job's output |
| `app/demo`, `app/subscribe`, `app/pipeline` | Razorpay Checkout, and a 1s poll on a live case |

That split is also what makes CORS matter for some pages and not others. Server
components fetch from Node, where no browser policy applies; the control room and the
demo routes fetch from the browser, so `app/main.py` allows the dev origins explicitly —
never `*`, because this API is unauthenticated by design on a single-operator local app.

## Two surfaces that are not the console

`/subscribe` is the customer's view of a failed charge and `/pipeline` is the operator
window it pops open beside itself. Neither gets the nine-item rail: one is a different
product with a different name on it, the other is a companion panel, not a destination.

`components/Shell.tsx` decides this from the pathname. A `(console)` route group would be
the more idiomatic App Router answer and was the alternative considered; it was not worth
renaming every existing path to add two pages, when the rail was already a client
component reading the pathname for its active state.

## The typed client

`lib/schema.d.ts` is generated, and regenerating it is two commands:

```bash
python scripts/dump_openapi.py --out web/lib/openapi.json
cd web && npx openapi-typescript lib/openapi.json -o lib/schema.d.ts
```

A renamed route then breaks `npm run build` instead of a page.

**What it does not give us**, stated plainly rather than implied away: every dashboard
route is annotated `-> dict[str, Any]` in Python, so OpenAPI describes each body as an
open object. The domain types in `lib/types.ts` are hand-written and are *asserted*
against `app/metrics.py`, not proven by it. The routes, their query parameters and
their methods are genuinely checked; the response fields are a convention.

The one bug that has already cost us here: `GET /api/cases` aliases its columns
(`id AS case_id`, `current_category AS category`) and `GET /api/cases/{id}` does not.
They are two shapes and `types.ts` now says so — collapsing them behind one optimistic
type rendered `undefined` into a table cell.

## Two rules this UI does not bend

1. **A number the backend could not measure never renders as `0`.** `<Unmeasured />`
   exists for exactly that: a category with an empty control arm reads *not
   measurable*. "No effect" and "no measurement" are the distinction this whole project
   turns on, and a reader must be able to tell them apart.
2. **An estimate never renders without its interval.** `<Interval />` takes both or
   neither, so there is no component capable of showing a point estimate alone. An
   interval that spans zero is labelled as spanning zero rather than rounded into a win.

## Parity (task 5.5)

`dashboard/` is deleted only once every one of its nine sections has an equivalent
here. Both ship until then. Three routes here have no counterpart over there at all:

| Route | What it is |
|---|---|
| `/queue` | the handoff queue — we charge ₹40 a case and had no screen for it |
| `/cases/[id]` | the voice-and-promise timeline, with the inbound reading |
| `/fencing` | the compensation log and the per-phase fence verdicts |

## Design

Tokens in `app/globals.css` are ported **verbatim** from `dashboard/style.css`. Every
contrast ratio in those comments was measured, and the held-out arm already had its own
colour throughout — the one place colour carries meaning, and it always ships with the
word "control" beside it. Re-picking a palette during a framework swap would have
thrown that away for nothing.
