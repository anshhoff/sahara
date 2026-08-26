"""The append-only audit trail (docs/03 §7, docs/07 §1).

There is exactly one writer, `audit()`, and no UPDATE or DELETE against audit_log
anywhere in app/ — tests/test_audit_append_only.py greps the package and fails the
build if one ever appears.
"""
from __future__ import annotations

import json
from typing import Any

from app import clock, db

STAGES = ("detect", "diagnose", "decide", "execute", "stop", "outcome")
ACTORS = ("system", "llm", "razorpay", "human")


def audit(case_id: str, stage: str, actor: str, summary: str, detail: dict[str, Any] | None = None) -> int:
    """Append one entry. Returns the per-case seq it was written at.

    `seq` is allocated as MAX(seq)+1 for the case inside the same transaction as the
    insert, and (case_id, seq) is UNIQUE, so the trail is gapless from 1 and cannot
    interleave incorrectly.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown audit stage {stage!r}")
    if actor not in ACTORS:
        raise ValueError(f"unknown audit actor {actor!r}")

    with db._lock:  # one transaction: allocate seq and insert
        conn = db.get()
        synthetic = conn.execute(
            "SELECT synthetic FROM recovery_case WHERE id = ?", (case_id,)
        ).fetchone()
        synth = int(synthetic[0]) if synthetic is not None else 0
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_log WHERE case_id = ?", (case_id,)
        ).fetchone()
        seq = int(row[0])
        conn.execute(
            "INSERT INTO audit_log (case_id, seq, stage, actor, summary, detail, created_at, synthetic)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                seq,
                stage,
                actor,
                summary,
                json.dumps(detail or {}, default=str, sort_keys=True),
                clock.now_iso(),
                synth,
            ),
        )
        conn.commit()
    return seq


def trail(case_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM audit_log WHERE case_id = ? ORDER BY seq ASC", (case_id,)
    )
    out = []
    for r in db.rows_to_dicts(rows):
        try:
            r["detail"] = json.loads(r["detail"])
        except (TypeError, ValueError):
            r["detail"] = {"raw": r["detail"]}
        out.append(r)
    return out
