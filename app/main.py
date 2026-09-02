"""FastAPI application: webhook receiver + dashboard API + static dashboard, in one
process (docs/01 §1). No job queue, no worker pool, no second service.

The background tick loop is a plain asyncio task on a 30s interval. In batch mode the
runner calls executor.tick() directly against a simulated clock instead — same
function, same code path, different clock.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import api, clock, config, control, db, executor, llm, webhooks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("recovery-agent")

TICK_INTERVAL_SECONDS = int(os.environ.get("TICK_INTERVAL_SECONDS", "30"))
DASHBOARD_DIR = Path(config.ROOT) / "dashboard"

app = FastAPI(
    title="Failed Subscription Recovery Agent",
    description="Razorpay AI Buildathon — Track 03: AI Revenue Recovery",
    version="1.0",
)
app.include_router(api.router)
if config.CONTROL_ENABLED:
    # The dashboard's control room. Off in one place; see app/control.py.
    app.include_router(control.router)

_tick_task: asyncio.Task | None = None


async def _tick_loop() -> None:
    while True:
        try:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            # Synthetic rows are excluded: they belong to a batch replay that ran on a
            # simulated clock, and this loop runs on the wall clock. See executor.tick().
            result = await asyncio.to_thread(executor.tick, False)
            if result["executions"] or result["promises_lapsed"] or result["episodes_expired"]:
                log.info(
                    "tick: %d executed, %d promises lapsed, %d episodes expired",
                    len(result["executions"]), result["promises_lapsed"], result["episodes_expired"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # a tick failure must never kill the loop
            log.exception("tick failed")


@app.on_event("startup")
async def startup() -> None:
    db.init()
    log.info("database ready at %s", config.DB_PATH)
    log.info("LLM provider: %s (copy drafting %s)",
             config.LLM_PROVIDER, "on" if config.LLM_COPY_ENABLED else "off")
    problem = llm.misconfiguration()
    if problem:
        log.warning("LLM MISCONFIGURED — %s  Every ambiguous case will stop as `unknown`. "
                    "Run `python scripts/check_llm.py` to confirm.", problem)
    global _tick_task
    _tick_task = asyncio.create_task(_tick_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _tick_task is not None:
        _tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _tick_task


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> Response:
    """Signature failure returns 400 and touches nothing — no row of any kind is
    created for an unverified body."""
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    if not webhooks.verify_signature(raw, signature):
        log.warning("rejected webhook: signature verification failed")
        return JSONResponse({"status": "rejected", "reason": "invalid signature"}, status_code=400)

    try:
        payload = __import__("json").loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"status": "rejected", "reason": "body is not JSON"}, status_code=400)

    result = await asyncio.to_thread(
        webhooks.intake, payload, "webhook", dict(request.headers)
    )
    # Always 2xx once the signature is verified: a non-2xx makes Razorpay redeliver,
    # and intake is already idempotent on the event id.
    return JSONResponse({k: v for k, v in result.items() if k != "case"}, status_code=200)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "time": clock.now_iso()}


if DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
