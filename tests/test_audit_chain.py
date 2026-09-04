"""The audit log's hash chain (app/audit.py).

"Append-only" is a promise about the code, enforced by a grep. The chain is a
property of the data, and these tests are what make the difference real: they edit
and delete rows behind the writer's back, exactly as someone doctoring a result
would, and assert that verify() names the row.
"""
from __future__ import annotations

from app import audit, cases, db, policy


def _case():
    c = cases.create(subscription_id="SYNTH-sub-a", customer_id="SYNTH-cust-a",
                     amount_at_risk_paise=49900, synthetic=True)
    cases.set_category(c["id"], "card_expired")
    return cases.get(c["id"])


def _case_with_trail():
    """A case with a trail several entries long. `cases.create()` writes no audit entry
    of its own — the trail starts at the first stage that made a decision — so the
    entries are produced by running one, plus a note on the end to give the chain a
    row after the one these tests tamper with."""
    case = _case()
    policy.decide(case)
    audit.audit(case["id"], "detect", "human", "operator looked at this case", {"note": "n/a"})
    assert len(db.query("SELECT id FROM audit_log")) >= 3
    return case


def test_a_fresh_log_verifies_and_every_entry_is_chained(fresh_db):
    _case_with_trail()
    result = audit.verify()
    assert result["status"] == "intact"
    assert result["n_entries"] > 0
    assert result["n_checked"] == result["n_entries"]
    assert result["n_unchained"] == 0


def test_the_first_entry_hashes_against_the_genesis_constant(fresh_db):
    """Without an anchor, a log truncated to its first entry would verify perfectly."""
    _case_with_trail()
    first = db.query_one("SELECT prev_hash FROM audit_log ORDER BY id ASC LIMIT 1")
    assert first["prev_hash"] == audit.GENESIS_HASH


def test_editing_one_word_of_one_summary_breaks_the_chain(fresh_db):
    _case_with_trail()
    target = db.query_one("SELECT id FROM audit_log ORDER BY id ASC LIMIT 1 OFFSET 1")
    db.execute("UPDATE audit_log SET summary = 'nothing to see here' WHERE id = ?", (target["id"],))

    result = audit.verify()
    assert result["status"] == "broken"
    assert result["first_break"]["id"] == target["id"]
    assert result["first_break"]["recorded_hash"] != result["first_break"]["recomputed_hash"]


def test_deleting_an_entry_breaks_the_chain(fresh_db):
    """The reason the chain spans the whole log rather than one case at a time. A
    per-case chain notices an edit inside a trail and misses the deletion of a trail,
    which is the more attractive thing to delete: it removes the inconvenient number
    from the metrics as well as its explanation."""
    _case_with_trail()
    victim = db.query_one("SELECT id FROM audit_log ORDER BY id ASC LIMIT 1 OFFSET 1")
    db.execute("DELETE FROM audit_log WHERE id = ?", (victim["id"],))

    result = audit.verify()
    assert result["status"] == "broken"
    # The entry AFTER the hole is where it shows: its recorded predecessor is a row
    # that no longer exists.
    assert result["first_break"]["recorded_prev_hash"] != result["first_break"]["expected_prev_hash"]


def test_rewriting_an_entry_and_its_own_hash_still_breaks_the_chain(fresh_db):
    """The interesting attack: someone who reads audit.py and recomputes the hash of
    the row they edited. It still fails, because every LATER row was hashed against
    the old value."""
    _case_with_trail()
    rows = db.rows_to_dicts(db.query("SELECT * FROM audit_log ORDER BY id ASC"))
    target = rows[1]
    target["summary"] = "a plausible-looking substitute"
    forged = audit.entry_hash(target, target["prev_hash"])
    db.execute("UPDATE audit_log SET summary = ?, entry_hash = ? WHERE id = ?",
               (target["summary"], forged, target["id"]))

    result = audit.verify()
    assert result["status"] == "broken"
    assert result["first_break"]["id"] == rows[2]["id"]


def test_head_is_a_fingerprint_that_moves_with_every_entry(fresh_db):
    case = _case_with_trail()
    before = audit.head()
    audit.audit(case["id"], "detect", "system", "one more thing", {})
    assert audit.head() != before
    assert audit.verify()["head"] == audit.head()


def test_unchained_rows_are_reported_rather_than_passed_silently(fresh_db):
    """A row written before the chain existed cannot be retro-hashed without inventing
    history, so it is counted, not skipped. An unverifiable entry folded into a
    passing result is the exact thing verify() exists to stop."""
    _case_with_trail()
    db.execute("UPDATE audit_log SET prev_hash = NULL, entry_hash = NULL WHERE id ="
               " (SELECT id FROM audit_log ORDER BY id ASC LIMIT 1)")
    result = audit.verify()
    assert result["n_unchained"] == 1
    # The rows that DO carry hashes still verify against each other. The unverifiable
    # link across the gap is reported as a gap, not as a forgery.
    assert result["status"] == "intact"
    assert result["n_checked"] == result["n_entries"] - 1
