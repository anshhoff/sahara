"""The append-only audit trail (docs/03 §7, docs/07 §1), hash-chained.

There is exactly one writer, `audit()`, and no UPDATE or DELETE against audit_log
anywhere in app/ — tests/test_llm_boundary.py greps the package and fails the build
if one ever appears.

Append-only-by-convention is a promise about the code. The chain below is a property
of the data: every entry carries a SHA-256 over its own contents together with the
previous entry's hash, so editing one word of one summary, or deleting one row,
invalidates every hash after it. `verify()` finds the first break and names it.

The chain runs across the WHOLE log in insertion order, not per case. A per-case
chain would detect an edit inside a case's trail but not the deletion of an entire
case — which is the more attractive thing to delete, because it is the one that
removes an inconvenient number from the metrics as well as its explanation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app import clock, db

STAGES = ("detect", "diagnose", "decide", "execute", "stop", "outcome")
ACTORS = ("system", "llm", "razorpay", "human")

# The chain's anchor. Nothing precedes the first entry, so it hashes against a
# constant rather than against nothing — otherwise a log truncated to its first entry
# would verify perfectly.
GENESIS_HASH = "0" * 64

# The fields the hash covers. `id` is excluded because SQLite assigns it and it is
# positional, not content; everything a reader would care about having been altered
# is in here.
_HASHED_FIELDS = ("case_id", "seq", "stage", "actor", "summary", "detail", "created_at", "synthetic")


def entry_hash(row: dict[str, Any], prev_hash: str) -> str:
    """sha256(prev_hash ‖ canonical_json(row)). Canonical means sorted keys and no
    incidental whitespace, so the same entry hashes identically on any machine and in
    any Python version."""
    body = json.dumps({k: row.get(k) for k in _HASHED_FIELDS}, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def head() -> str:
    """The hash of the most recent entry — a one-line fingerprint of this database.

    Record it before handing the file to someone and you can tell afterwards whether
    anything in it moved. It is not a reproducibility check: case ids carry a random
    ULID tail, so two clean-room runs of the same seed produce identical numbers and
    different hashes. The seed is what reproduces; this is what detects tampering.
    """
    row = db.query_one("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
    if row is None or row["entry_hash"] is None:
        return GENESIS_HASH
    return str(row["entry_hash"])


def audit(case_id: str, stage: str, actor: str, summary: str, detail: dict[str, Any] | None = None) -> int:
    """Append one entry. Returns the per-case seq it was written at.

    `seq` is allocated as MAX(seq)+1 for the case inside the same transaction as the
    insert, and (case_id, seq) is UNIQUE, so the trail is gapless from 1 and cannot
    interleave incorrectly. The chain link is computed in that same transaction, under
    the same lock, which is what stops two concurrent writers from reading the same
    predecessor and forking the chain.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown audit stage {stage!r}")
    if actor not in ACTORS:
        raise ValueError(f"unknown audit actor {actor!r}")

    with db._lock:  # one transaction: read the chain head, allocate seq, insert
        conn = db.get()
        synthetic = conn.execute(
            "SELECT synthetic FROM recovery_case WHERE id = ?", (case_id,)
        ).fetchone()
        synth = int(synthetic[0]) if synthetic is not None else 0
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_log WHERE case_id = ?", (case_id,)
        ).fetchone()
        seq = int(row[0])

        prev = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = GENESIS_HASH if prev is None or prev[0] is None else str(prev[0])

        entry = {
            "case_id": case_id,
            "seq": seq,
            "stage": stage,
            "actor": actor,
            "summary": summary,
            "detail": json.dumps(detail or {}, default=str, sort_keys=True),
            "created_at": clock.now_iso(),
            "synthetic": synth,
        }
        conn.execute(
            "INSERT INTO audit_log (case_id, seq, stage, actor, summary, detail, created_at,"
            " prev_hash, entry_hash, synthetic)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["case_id"], entry["seq"], entry["stage"], entry["actor"],
                entry["summary"], entry["detail"], entry["created_at"],
                prev_hash, entry_hash(entry, prev_hash), entry["synthetic"],
            ),
        )
        conn.commit()
    return seq


def verify() -> dict[str, Any]:
    """Recompute the whole chain and report the first entry that does not match.

    Three outcomes, and the difference between them matters:

    * `intact` — every chained entry hashes to what it claims.
    * `broken` — an entry's contents no longer produce its recorded hash, or its
      recorded predecessor is not the entry that actually precedes it. `first_break`
      names the row. Everything after a break is untrustworthy by construction, so
      only the first one is reported.
    * `unchained` rows — written before this column existed. They are counted and
      reported separately rather than skipped, because an unverifiable entry silently
      folded into a passing result is exactly the thing this function exists to stop.

    A run of unchained rows breaks the anchor: there is no hash on the far side of the
    gap to link across, so the next chained row's recorded predecessor is ADOPTED
    rather than checked, and verification resumes from there. That link is genuinely
    unverifiable, and pretending otherwise — by expecting the genesis constant on the
    other side of a gap — would report a break where there is only missing evidence.
    The gap itself is never hidden: it is `n_unchained`.
    """
    rows = db.rows_to_dicts(db.query("SELECT * FROM audit_log ORDER BY id ASC"))
    prev_hash: Optional[str] = GENESIS_HASH
    unchained = 0
    checked = 0
    for row in rows:
        if row.get("entry_hash") is None:
            unchained += 1
            prev_hash = None          # the anchor is gone; re-anchor on the next row
            continue
        if prev_hash is None:
            prev_hash = row.get("prev_hash")
        expected = entry_hash(row, prev_hash)
        if row.get("prev_hash") != prev_hash or row["entry_hash"] != expected:
            return {
                "status": "broken",
                "n_entries": len(rows),
                "n_checked": checked,
                "n_unchained": unchained,
                "first_break": {
                    "id": row["id"],
                    "case_id": row["case_id"],
                    "seq": row["seq"],
                    "stage": row["stage"],
                    "recorded_hash": row["entry_hash"],
                    "recomputed_hash": expected,
                    "recorded_prev_hash": row.get("prev_hash"),
                    "expected_prev_hash": prev_hash,
                },
                "head": head(),
            }
        prev_hash = row["entry_hash"]
        checked += 1
    return {
        "status": "intact",
        "n_entries": len(rows),
        "n_checked": checked,
        "n_unchained": unchained,
        "first_break": None,
        "head": prev_hash,
    }


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
