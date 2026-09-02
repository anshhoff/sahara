"""Control room — the operations that used to require a terminal, exposed as API.

Everything here already existed as a script or a test: `pytest`, `generate_synthetic.py`,
`run_batch.py`, `executor.tick()`. This module does not reimplement any of them. It runs
the *same* commands as subprocesses and streams their output, so what the dashboard shows
is the real thing and not a rehearsal of it — a claim that survives someone running the
command themselves and getting identical output.

Two rules hold this together:

* **Nothing here is a new code path into the pipeline.** The storm and the injector both
  enter through `webhooks.intake()`, exactly like a live Razorpay delivery.
* **One job at a time.** The batch resets the database file underneath a running server,
  so a second concurrent job would be reading a database that is being deleted. The
  server's connection is re-opened after every job that touches the file.

Unauthenticated, like the rest of this API. `CONTROL_API_ENABLED=false` turns the whole
router off in one place (config.CONTROL_ENABLED).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import cases, clock, config, db, executor, metrics, webhooks

router = APIRouter(prefix="/api/control", tags=["control"])

MAX_LINES = 4000  # a runaway subprocess must not grow the process without bound


# --------------------------------------------------------------------- jobs
class Job:
    """One subprocess run, with its output kept in memory for the dashboard to poll."""

    def __init__(self, kind: str, label: str, steps: list[list[str]],
                 env: Optional[dict[str, str]] = None):
        self.env = env
        self.id = f"job_{uuid.uuid4().hex[:12]}"
        self.kind = kind
        self.label = label
        self.steps = steps
        self.status = "running"          # running | passed | failed | error
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.lines: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {}
        self._lock = threading.Lock()

    # -- output
    def emit(self, text: str, stream: str = "out") -> None:
        with self._lock:
            if len(self.lines) >= MAX_LINES:
                if len(self.lines) == MAX_LINES:
                    self.lines.append({"n": len(self.lines), "stream": "meta",
                                       "text": f"… output truncated at {MAX_LINES} lines"})
                return
            self.lines.append({"n": len(self.lines), "stream": stream, "text": text.rstrip("\n")})

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        with self._lock:
            lines = [ln for ln in self.lines if ln["n"] >= after]
            return {
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed_seconds": round((self.finished_at or time.time()) - self.started_at, 2),
                "lines": lines,
                "next": len(self.lines),
                "result": self.result,
                "command": [" ".join(s) for s in self.steps],
            }


_jobs: dict[str, Job] = {}
_job_order: list[str] = []
_active: Optional[str] = None
_job_lock = threading.Lock()


def _active_job() -> Optional[Job]:
    with _job_lock:
        return _jobs.get(_active) if _active else None


def _start(job: Job, after: Optional[Any] = None, reopen_db: bool = False) -> Job:
    global _active
    with _job_lock:
        running = _jobs.get(_active) if _active else None
        if running is not None and running.status == "running":
            raise HTTPException(status_code=409,
                                detail=f"{running.label} is still running; wait for it or reload")
        _jobs[job.id] = job
        _job_order.append(job.id)
        for stale in _job_order[:-12]:            # keep the last dozen runs, drop the rest
            _jobs.pop(stale, None)
        del _job_order[:-12]
        _active = job.id

    threading.Thread(target=_run, args=(job, after, reopen_db), daemon=True).start()
    return job


def _run(job: Job, after: Optional[Any], reopen_db: bool) -> None:
    try:
        code = 0
        for step in job.steps:
            job.emit(f"$ {' '.join(step)}", stream="cmd")
            code = _stream(job, step, job.env)
            if code != 0:
                break
        job.status = "passed" if code == 0 else "failed"
    except Exception as exc:  # a broken control action must not take the server with it
        job.emit(f"{type(exc).__name__}: {exc}", stream="err")
        job.status = "error"
    finally:
        if reopen_db:
            # run_batch.py deletes and recreates the database file. This process is still
            # holding a connection to the old inode, so every query after it would read a
            # database nobody is writing to any more.
            try:
                db.close()
                db.init()
            except Exception as exc:  # pragma: no cover - only on a broken filesystem
                job.emit(f"could not re-open the database: {exc}", stream="err")
        if after is not None:
            try:
                job.result = after(job) or {}
            except Exception as exc:
                job.emit(f"post-step failed: {type(exc).__name__}: {exc}", stream="err")
        job.finished_at = time.time()


def _stream(job: Job, argv: list[str], env: Optional[dict[str, str]] = None) -> int:
    proc = subprocess.Popen(
        argv, cwd=str(config.ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        job.emit(line)
    return proc.wait()


# ------------------------------------------------------------------ guards
def _guard() -> None:
    if not config.CONTROL_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="control API disabled (CONTROL_API_ENABLED=false)",
        )


# ------------------------------------------------------------------- tests
_PYTEST_LINE = re.compile(
    r"^(?P<file>tests/\S+\.py)::(?P<name>\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
)
_PYTEST_TOTALS = re.compile(r"^=+\s+(?P<body>.*?(passed|failed|error).*?)\s+=+$")


def _summarise_tests(job: Job) -> dict[str, Any]:
    tests: list[dict[str, str]] = []
    totals = ""
    for line in job.lines:
        m = _PYTEST_LINE.match(line["text"])
        if m:
            tests.append({"file": m.group("file"), "name": m.group("name"),
                          "outcome": m.group("outcome")})
            continue
        t = _PYTEST_TOTALS.match(line["text"].strip())
        if t:
            totals = t.group("body")
    counts: dict[str, int] = {}
    for t in tests:
        counts[t["outcome"].lower()] = counts.get(t["outcome"].lower(), 0) + 1
    return {"tests": tests, "counts": counts, "totals": totals, "n": len(tests)}


class TestsRequest(BaseModel):
    path: Optional[str] = Field(
        default=None,
        description="a single test file under tests/, or null for the whole suite",
    )


@router.post("/tests")
def run_tests(req: TestsRequest) -> dict[str, Any]:
    """Run the suite exactly as CI would: same interpreter, same working directory."""
    _guard()
    target = "tests"
    if req.path:
        # Only ever a path inside tests/ — this endpoint runs a subprocess, so the
        # argument is constrained to a known directory rather than trusted.
        safe = req.path.strip().lstrip("/")
        if not safe.startswith("tests/") or ".." in safe:
            raise HTTPException(status_code=400, detail="path must be inside tests/")
        if not (config.ROOT / safe).exists():
            raise HTTPException(status_code=404, detail=f"no such test file: {safe}")
        target = safe
    argv = [sys.executable, "-m", "pytest", target, "-v", "--no-header",
            "-p", "no:cacheprovider", "--color=no"]
    # conftest.py pins LLM_PROVIDER=none with os.environ.setdefault, which wins over .env
    # in a developer's shell but not here: this server process has already loaded .env, so
    # the child would inherit a real provider and the suite would both hit a live model and
    # fail the assertions that check the no-model path. Pinning it makes a run started from
    # the dashboard identical to a run started from a terminal.
    env = dict(os.environ)
    env["LLM_PROVIDER"] = "none"
    job = Job("tests", f"pytest {target}", [argv], env=env)
    return _start(job, after=_summarise_tests).snapshot()


# ------------------------------------------------------------------- batch
class BatchRequest(BaseModel):
    n: int = Field(default=80, ge=10, le=400)
    seed: int = Field(default=42)
    holdout: float = Field(default=0.35, ge=0.0, le=0.6,
                           description="fraction of cases assigned to the untouched control arm")
    live_links: int = Field(default=0, ge=0, le=5,
                            description="real test-mode Payment Links to spend; 0 keeps the run offline")


@router.post("/batch")
def run_batch(req: BatchRequest) -> dict[str, Any]:
    """Regenerate the synthetic cases and replay the whole batch, resetting the database.

    This is the README's reproduce command, run verbatim — including the seed, so the
    run is the same one a judge gets from their own terminal.
    """
    _guard()
    py = sys.executable
    steps = [
        [py, "scripts/generate_synthetic.py", "--n", str(req.n), "--seed", str(req.seed),
         "--out", "synthetic_cases.json"],
        [py, "scripts/run_batch.py", "--cases", "synthetic_cases.json",
         "--db", config.DB_PATH, "--holdout", str(req.holdout),
         "--live-links", str(req.live_links)],
    ]
    job = Job("batch", f"batch n={req.n} seed={req.seed} holdout={req.holdout}", steps)
    return _start(job, after=lambda _j: {"summary": metrics.summary()}, reopen_db=True).snapshot()


# -------------------------------------------------------------------- jobs API
@router.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    _guard()
    with _job_lock:
        jobs = [_jobs[i] for i in _job_order if i in _jobs]
    return [{k: v for k, v in j.snapshot().items() if k != "lines"} for j in reversed(jobs)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, after: int = 0) -> dict[str, Any]:
    """Poll for output. `after` is the `next` cursor from the previous poll, so the
    dashboard streams a long run without re-sending what it already has."""
    _guard()
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job.snapshot(after=after)


# -------------------------------------------------------------------- the loop
@router.post("/tick")
def tick() -> dict[str, Any]:
    """Run one iteration of the loop by hand, instead of waiting out the 30s timer."""
    _guard()
    result = executor.tick()
    return {
        "at": result["at"],
        "n_executed": len(result["executions"]),
        "promises_lapsed": result["promises_lapsed"],
        "episodes_expired": result["episodes_expired"],
        "executions": [
            {"case_id": e["case_id"], "action": e["action"], "mode": e["mode"], "status": e["status"]}
            for e in result["executions"]
        ],
    }


# ------------------------------------------------------------------ adversarial
def _demo_payload(*, event_id: str, sub_id: str, cust_id: str, amount_paise: int,
                  error: tuple[str, str, str, str]) -> dict[str, Any]:
    """A Razorpay `payment.failed` body, shaped exactly like the real one. Marked
    synthetic, so it can never be counted as live recovery."""
    code, reason, description, source = error
    now = int(clock.now().timestamp())
    return {
        "entity": "event", "event": "payment.failed", "contains": ["payment", "subscription"],
        "payload": {
            "payment": {"entity": {
                "id": f"pay_DEMO{event_id[-8:]}", "entity": "payment", "amount": amount_paise,
                "currency": "INR", "status": "failed", "method": "card", "customer_id": cust_id,
                "created_at": now, "error_code": code, "error_reason": reason,
                "error_description": description, "error_source": source,
                "error_step": "payment_authorization",
                "notes": {"subscription_id": sub_id, "customer_id": cust_id},
            }},
            "subscription": {"entity": {"id": sub_id, "entity": "subscription",
                                        "customer_id": cust_id, "status": "active"}},
        },
        "created_at": now, "id": event_id, "synthetic": True,
    }


_DEMO_ERRORS = {
    "card_expired": ("BAD_REQUEST_ERROR", "payment_failed", "Your card has expired", "customer"),
    "insufficient_funds": ("BAD_REQUEST_ERROR", "payment_failed",
                           "Your card has insufficient funds", "bank"),
    "issuer_declined": ("BAD_REQUEST_ERROR", "payment_failed",
                        "Payment was declined by the issuing bank", "issuer"),
    "authentication_failed": ("BAD_REQUEST_ERROR", "payment_failed",
                              "3DS authentication failed", "customer"),
    "invalid_payment_method": ("BAD_REQUEST_ERROR", "payment_failed",
                               "The card number is invalid", "customer"),
    "unknown": ("GATEWAY_ERROR", "payment_failed", "", "gateway"),
}


class StormRequest(BaseModel):
    n: int = Field(default=10, ge=2, le=50)
    distinct: bool = Field(
        default=False,
        description="false: n copies of ONE event (delivery retry). true: n different events "
                    "for one subscription (a burst of real failures).",
    )


@router.post("/storm")
def storm(req: StormRequest) -> dict[str, Any]:
    """Fire n webhook deliveries simultaneously, against the live database.

    This is `tests/test_concurrency.py` run against the running server instead of a temp
    database: the same barrier, the same `intake()`. Duplicate delivery must collapse to
    one case; a burst of distinct failures on one subscription must still be one case,
    because two open cases would mean two independent attempt budgets against the same
    customer.
    """
    _guard()
    if _active_job() is not None and _active_job().status == "running":
        raise HTTPException(status_code=409, detail="a job is running; wait for it to finish")

    stamp = uuid.uuid4().hex[:8].upper()
    sub_id = f"sub_STORM{stamp}"
    payloads = [
        _demo_payload(
            event_id=f"evt_STORM{stamp}" + (f"_{i}" if req.distinct else ""),
            sub_id=sub_id, cust_id=f"cust_STORM{stamp}", amount_paise=49900,
            error=_DEMO_ERRORS["insufficient_funds"],
        )
        for i in range(req.n)
    ]

    barrier = threading.Barrier(req.n)
    outcomes: list[dict[str, Any]] = []
    errors: list[str] = []
    lock = threading.Lock()

    def fire(i: int) -> None:
        barrier.wait()
        try:
            out = webhooks.intake(payloads[i], source="synthetic")
        except BaseException as exc:  # noqa: BLE001 — what escapes intake is the finding
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")
        else:
            with lock:
                outcomes.append({k: v for k, v in out.items() if k != "case"})

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(req.n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    statuses: dict[str, int] = {}
    for o in outcomes:
        statuses[o["status"]] = statuses.get(o["status"], 0) + 1

    n_cases = int(db.scalar("SELECT COUNT(*) FROM recovery_case WHERE subscription_id = ?",
                            (sub_id,), 0))
    n_events = int(db.scalar("SELECT COUNT(*) FROM failure_event WHERE subscription_id = ?",
                             (sub_id,), 0))
    # Live decisions only. A fresh failure on an open case legitimately supersedes the
    # decision that was already scheduled — what must never happen is two of them
    # standing at once, because that is two interventions aimed at one customer.
    n_decisions = int(db.scalar(
        "SELECT COUNT(*) FROM intervention_decision WHERE status IN ('scheduled','executed')"
        " AND case_id IN (SELECT id FROM recovery_case WHERE subscription_id = ?)", (sub_id,), 0))
    case = cases.find_latest_by_subscription(sub_id)

    expected_events = req.n if req.distinct else 1
    return {
        "n_fired": req.n,
        "distinct": req.distinct,
        "subscription_id": sub_id,
        "case_id": case["id"] if case else None,
        "responses": statuses,
        "errors": errors,
        "observed": {"cases": n_cases, "events": n_events, "decisions": n_decisions},
        "expected": {"cases": 1, "events": expected_events, "decisions": 1},
        "held": (not errors and n_cases == 1 and n_events == expected_events and n_decisions == 1),
        "explains": (
            "n identical deliveries of one event: the UNIQUE event id collapses them to a "
            "single stored event, one case, one decision."
            if not req.distinct else
            "n distinct events for one subscription: every event is stored, but they belong "
            "to one recovery episode, so there is exactly one case and one live decision — "
            "each new failure supersedes the decision before it rather than adding to it."
        ),
    }


class InjectRequest(BaseModel):
    category: str = Field(default="card_expired")
    amount_rupees: float = Field(default=499.0, gt=0, le=100000)


@router.post("/inject")
def inject(req: InjectRequest) -> dict[str, Any]:
    """Push one failure through the live pipeline and hand back the case it opened.

    The payload is a real Razorpay event body and it enters through `intake()`, so this
    walks detect → diagnose → gate → decide exactly like a delivery from Razorpay. The
    only difference is `source`.
    """
    _guard()
    if req.category not in config.CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"category must be one of {list(config.CATEGORIES)}")
    stamp = uuid.uuid4().hex[:8].upper()
    payload = _demo_payload(
        event_id=f"evt_DEMO{stamp}", sub_id=f"sub_DEMO{stamp}", cust_id=f"cust_DEMO{stamp}",
        amount_paise=int(round(req.amount_rupees * 100)),
        error=_DEMO_ERRORS[req.category],
    )
    result = webhooks.intake(payload, source="synthetic")
    case_id = result.get("case_id")
    return {
        "status": result["status"],
        "case_id": case_id,
        "requested_category": req.category,
        "case": cases.get(case_id) if case_id else None,
        "note": "entered through intake() — the same function the live webhook calls",
    }


# -------------------------------------------------------------------- state
@router.get("/state")
def state() -> dict[str, Any]:
    """What the control room is allowed to do, and what it is doing right now."""
    job = _active_job()
    return {
        "enabled": config.CONTROL_ENABLED,
        "db_path": config.DB_PATH,
        "llm_provider": config.LLM_PROVIDER,
        "razorpay_configured": bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET),
        "live_link_budget": executor.live_link_budget(),
        "clock": {"now": clock.now_iso(), "simulated": bool(getattr(clock.get_clock(), "simulated", False))},
        "active_job": None if job is None else {k: v for k, v in job.snapshot().items() if k != "lines"},
        "categories": list(config.CATEGORIES),
        "test_files": sorted(p.name for p in (config.ROOT / "tests").glob("test_*.py")),
    }
