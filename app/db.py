"""SQLite access layer. Hand-written SQL, no ORM (docs/10 §9).

One process, one file, one connection guarded by a lock. `PRAGMA foreign_keys=ON`
is set per connection, because SQLite defaults it off and the schema's foreign keys
are part of the audit story.
"""
from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from app import config

_conn: Optional[sqlite3.Connection] = None
_lock = threading.RLock()
_current_path: Optional[str] = None

# ---------------------------------------------------------------- id generation
# A small ULID: 48-bit millisecond timestamp + 80 bits of randomness, Crockford
# base32, 26 chars, lexicographically sortable. Implemented here rather than
# pulling python-ulid, which docs/10 §2 explicitly marks as substitutable.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_id_lock = threading.Lock()
_last_ms = 0
_seq = 0


def _ulid() -> str:
    global _last_ms, _seq
    with _id_lock:
        ms = int(time.time() * 1000)
        if ms == _last_ms:
            _seq += 1
        else:
            _last_ms, _seq = ms, 0
        rand = (random.getrandbits(64) << 16) | (_seq & 0xFFFF)
    out = []
    v = ms
    for _ in range(10):
        out.append(_CROCKFORD[v & 31])
        v >>= 5
    ts = "".join(reversed(out))
    out = []
    v = rand
    for _ in range(16):
        out.append(_CROCKFORD[v & 31])
        v >>= 5
    return ts + "".join(reversed(out))


def new_id(prefix: str) -> str:
    """`case_01J...`, `evt_01J...` etc. (docs/03 conventions)."""
    return f"{prefix}_{_ulid()}"


# ------------------------------------------------------------------ connection
def connect(path: Optional[str] = None) -> sqlite3.Connection:
    global _conn, _current_path
    with _lock:
        # Once a path is opened it stays the active one, so a caller that passed an
        # explicit path (tests, the batch runner's --db) is never silently moved back
        # to the default database by a later argument-less get().
        target = path or _current_path or config.DB_PATH
        if _conn is not None and _current_path == target:
            return _conn
        if _conn is not None:
            _conn.close()
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _conn, _current_path = conn, target
        return conn


def get() -> sqlite3.Connection:
    return connect()


def close() -> None:
    global _conn, _current_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _current_path = None, None


def init(path: Optional[str] = None) -> sqlite3.Connection:
    """Apply schema.sql. Idempotent — every statement is CREATE ... IF NOT EXISTS."""
    conn = connect(path)
    with open(config.SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database created by an older schema up to date.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists, so a column
    added to schema.sql never reaches a database someone already has. Each step below
    is idempotent and additive; none rewrites or drops existing rows.

    (The status CHECK constraint cannot be widened in place without rebuilding the
    table. An older database therefore accepts the new is_holdout column but would
    reject a `stopped_holdout` status — which is correct: that database has no control
    arm in it, so nothing can legitimately land in that state. A fresh run gets the
    full constraint.)
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(recovery_case)").fetchall()}
    if have and "is_holdout" not in have:
        conn.execute(
            "ALTER TABLE recovery_case ADD COLUMN is_holdout INTEGER NOT NULL DEFAULT 0"
        )


def reset(path: Optional[str] = None) -> sqlite3.Connection:
    """Delete the database file and re-apply the schema (clean-room runs, tests)."""
    target = path or config.DB_PATH
    close()
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(target + suffix)
        except FileNotFoundError:
            pass
    return init(target)


# ----------------------------------------------------------------- small helpers
def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        return list(get().execute(sql, tuple(params)).fetchall())


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
    row = query_one(sql, params)
    if row is None:
        return default
    v = row[0]
    return default if v is None else v


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        conn = get()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    with _lock:
        conn = get()
        conn.executemany(sql, [tuple(r) for r in rows])
        conn.commit()


def insert(table: str, values: dict[str, Any]) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))


def update(table: str, id_value: str, values: dict[str, Any], id_col: str = "id") -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    execute(f"UPDATE {table} SET {sets} WHERE {id_col} = ?", list(values.values()) + [id_value])


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return None if row is None else {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]
