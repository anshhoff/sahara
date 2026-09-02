"""Adversarial concurrency and crash recovery (docs/04 §6, docs/02 §6.6).

Every other test file drives the pipeline one step at a time. This one attacks the
two windows where a payments agent actually loses money: two deliveries of the same
event arriving at once, and a process dying between an irreversible side effect and
the record of it.

The claim is not that these races cannot happen. It is that when they do, the
outcome is *at most once* — one case, one Payment Link, one consumed attempt — and
never a second contact to a customer who already got one.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from app import cases, clock, config, db, diagnosis, executor, policy, webhooks
from tests.conftest import failure_payload

N_THREADS = 10


def _storm(fn, n: int = N_THREADS) -> tuple[list[Any], list[BaseException]]:
    """Run `fn(i)` on n threads released simultaneously by a barrier.

    The barrier matters: without it the threads start staggered by their own creation
    cost and the interleaving under test never happens.
    """
    barrier = threading.Barrier(n)
    results: list[Any] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(i: int) -> None:
        barrier.wait()
        try:
            out = fn(i)
        except BaseException as exc:  # noqa: BLE001 — the test is about what escapes
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(out)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker thread deadlocked"
    return results, errors


def _count(table: str, where: str = "1 = 1", params: tuple = ()) -> int:
    return int(db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", params, 0))


# ------------------------------------------------------- concurrent duplicate delivery
def test_ten_identical_webhooks_land_as_one_case(fresh_db):
    """Razorpay retries delivery on any non-2xx and can duplicate on its own. Ten
    simultaneous copies of one event must produce one case, not ten."""
    payload = failure_payload(event_id="SYNTH-evt-storm-1", sub_id="SYNTH-sub-storm")

    results, errors = _storm(lambda _i: webhooks.intake(payload, source="synthetic"))

    assert errors == [], f"intake raised under concurrent duplicate delivery: {errors}"
    assert len(results) == N_THREADS
    processed = [r for r in results if r["status"] == "processed"]
    duplicates = [r for r in results if r["status"] == "duplicate"]
    assert len(processed) == 1, f"{len(processed)} threads processed the same event"
    assert len(duplicates) == N_THREADS - 1

    assert _count("failure_event") == 1
    assert _count("recovery_case") == 1
    assert _count("diagnosis_result") == 1
    assert _count("intervention_decision") == 1


def test_distinct_events_same_subscription_open_one_case(fresh_db):
    """Ten *different* event ids for one subscription arriving together. Each is a
    genuine new event, but they belong to a single recovery episode — two open cases
    would mean two independent attempt budgets against the same customer."""
    payloads = [
        failure_payload(event_id=f"SYNTH-evt-storm-2-{i}", sub_id="SYNTH-sub-storm-2")
        for i in range(N_THREADS)
    ]

    _results, errors = _storm(lambda i: webhooks.intake(payloads[i], source="synthetic"))

    assert errors == [], f"intake raised under concurrent distinct deliveries: {errors}"
    assert _count("failure_event") == N_THREADS, "every distinct event must be stored"
    assert _count("recovery_case", "subscription_id = ?", ("SYNTH-sub-storm-2",)) == 1, (
        "concurrent first-failures opened more than one case for one subscription"
    )
    assert _count("recovery_case", "status = 'open'") == 1


# ------------------------------------------------------------- duplicate executor
def _scheduled_decision(sub_id: str = "SYNTH-sub-exec") -> dict[str, Any]:
    """Drive one case to exactly one scheduled, immediately-due decision."""
    # insufficient_funds/1 is RETRY_LATER at +24h, so the decision stays *scheduled*
    # instead of executing inline the way a zero-delay policy row does.
    webhooks.intake(failure_payload(event_id=f"SYNTH-evt-{sub_id}", sub_id=sub_id),
                    source="synthetic")
    due = executor.due_decisions(clock.to_iso(clock.plus_hours(clock.now(), 24 * 30)))
    assert len(due) == 1, f"expected one scheduled decision, got {len(due)}"
    return due[0]


def test_duplicate_executor_runs_the_intervention_once(fresh_db, monkeypatch):
    """The same scheduled decision picked up by ten workers at once — a tick racing
    the webhook path, or two overlapping ticks. The conditional UPDATE that claims the
    decision is the only thing standing between this and ten Payment Links."""
    decision = _scheduled_decision()

    calls: list[str] = []
    real = executor._HANDLERS[decision["action"]]

    def counting(case):
        calls.append(case["id"])
        return real(case)

    monkeypatch.setitem(executor._HANDLERS, decision["action"], counting)

    records, errors = _storm(lambda _i: executor.execute_decision(dict(decision)))

    assert errors == [], f"execute_decision raised under a duplicate-executor storm: {errors}"
    assert len(calls) == 1, f"the intervention ran {len(calls)} times"
    assert len([r for r in records if r is not None]) == 1, "more than one execution claimed"
    assert _count("execution_record") == 1
    assert cases.get(decision["case_id"])["attempt_count"] == 1, "an attempt was double-spent"


def test_i1_holds_when_every_attempt_is_claimed_at_once(fresh_db):
    """reserve_attempt() is I1's real enforcement point. Twenty threads racing for
    three slots must hand out exactly three."""
    case = cases.create(subscription_id="SYNTH-sub-race", customer_id="SYNTH-cust-race",
                        amount_at_risk_paise=49900, synthetic=True)

    won, errors = _storm(lambda _i: cases.reserve_attempt(case["id"], "RETRY_LATER"), n=20)

    assert errors == []
    assert sum(1 for w in won if w) == config.MAX_ATTEMPTS
    assert cases.get(case["id"])["attempt_count"] == config.MAX_ATTEMPTS


# ------------------------------------------------------------------ crash mid-flight
def test_crash_between_side_effect_and_record_never_contacts_twice(fresh_db, sim_clock, monkeypatch):
    """The worst window in the system: the intervention has already run against
    Razorpay — a charge re-attempted, a link created, a notification composed — and the
    process dies before the ExecutionRecord is written.

    The recovery posture here is deliberately at-most-once, not exactly-once. On the
    next tick the decision is already claimed, so nothing re-fires: the crash costs an
    audit record, which is recoverable, instead of a second message to a customer,
    which is not.
    """
    decision = _scheduled_decision(sub_id="SYNTH-sub-crash")

    calls: list[str] = []
    real = executor._HANDLERS[decision["action"]]

    def counting(case):
        calls.append(case["id"])
        return real(case)

    monkeypatch.setitem(executor._HANDLERS, decision["action"], counting)

    real_insert = db.insert

    def crashing_insert(table: str, values: dict[str, Any]) -> None:
        if table == "execution_record":
            raise RuntimeError("process killed after the side effect, before the record")
        return real_insert(table, values)

    monkeypatch.setattr(db, "insert", crashing_insert)

    sim_clock.advance(hours=25)  # the RETRY_LATER decision comes due
    with pytest.raises(RuntimeError):
        executor.tick()

    assert len(calls) == 1
    assert _count("execution_record") == 0, "the crash was meant to land before the record"

    # ---- the process comes back up
    monkeypatch.setattr(db, "insert", real_insert)
    executor.tick()

    assert len(calls) == 1, "a crashed attempt was re-executed; the customer was contacted twice"
    assert _count("execution_record") == 0
    assert cases.get(decision["case_id"])["attempt_count"] == 1, (
        "the crashed attempt must stay spent, not be silently refunded"
    )
    row = db.query_one("SELECT status FROM intervention_decision WHERE id = ?", (decision["id"],))
    assert row["status"] == "executed"


def test_orphaned_executions_are_detectable(fresh_db, sim_clock, monkeypatch):
    """An at-most-once system has to be able to *find* what it lost. A decision marked
    executed with no ExecutionRecord under it is exactly the crash above, and one query
    surfaces every one of them for the human queue."""
    decision = _scheduled_decision(sub_id="SYNTH-sub-orphan")
    real_insert = db.insert
    monkeypatch.setattr(
        db, "insert",
        lambda table, values: (_ for _ in ()).throw(RuntimeError("killed"))
        if table == "execution_record" else real_insert(table, values),
    )
    sim_clock.advance(hours=25)
    with pytest.raises(RuntimeError):
        executor.tick()
    monkeypatch.setattr(db, "insert", real_insert)

    orphans = db.rows_to_dicts(db.query(
        "SELECT d.id, d.case_id FROM intervention_decision d"
        " LEFT JOIN execution_record e ON e.decision_id = d.id"
        " WHERE d.status = 'executed' AND e.id IS NULL"
    ))
    assert [o["id"] for o in orphans] == [decision["id"]]


# ------------------------------------------------------------- clock isolation
def test_background_tick_leaves_batch_rows_alone(fresh_db, sim_clock):
    """Two clocks, one database.

    A batch replay writes its cases against a simulated clock. The live loop in main.py
    runs on the wall clock, where those same cases look weeks old and therefore expired —
    so an unfiltered background tick would close a finished experiment and report it as a
    batch that recovered nothing. Synthetic rows advance only under the clock that made
    them; live rows are still the background loop's job.
    """
    webhooks.intake(failure_payload(event_id="SYNTH-evt-clock", sub_id="SYNTH-sub-clock"),
                    source="synthetic")
    live_body = failure_payload(event_id="LIVE-evt-clock", sub_id="LIVE-sub-clock")
    live_body["synthetic"] = False          # a real Razorpay delivery carries no such flag
    webhooks.intake(live_body, source="webhook")
    live = cases.find_open_by_subscription("LIVE-sub-clock")
    synth = cases.find_open_by_subscription("SYNTH-sub-clock")
    assert live["synthetic"] == 0 and synth["synthetic"] == 1

    sim_clock.advance(days=config.EPISODE_WINDOW_DAYS + 1)

    background = executor.tick(include_synthetic=False)
    assert cases.get(synth["id"])["status"] == "open", "the background loop closed a batch case"
    assert all(e["case_id"] != synth["id"] for e in background["executions"])
    assert cases.get(live["id"])["status"] != "open", "the background loop skipped a live case"

    executor.tick()  # the operator's own Tick, or the batch runner
    assert cases.get(synth["id"])["status"] != "open"
