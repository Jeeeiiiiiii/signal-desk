"""Incident persistence.

SQLite because the app is not the point of this project. The consumer and the
web tier share one file; swapping it for Postgres is a connection-string change.
"""

import sqlite3
import threading

_local = threading.local()
DB_PATH = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    fingerprint     TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    severity        TEXT NOT NULL,
    status          TEXT NOT NULL,
    source          TEXT NOT NULL,
    target          TEXT,
    description     TEXT,
    first_seen      INTEGER NOT NULL,
    last_seen       INTEGER NOT NULL,
    occurrences     INTEGER NOT NULL DEFAULT 1,
    acked_by        TEXT,
    acked_at        INTEGER
);

-- At-least-once delivery means we will see the same message again. Rather than
-- guess whether a repeat is a retry or a genuine re-fire, we record every key
-- we have already applied and ignore repeats outright.
CREATE TABLE IF NOT EXISTS applied_keys (
    idempotency_key TEXT PRIMARY KEY,
    applied_at      INTEGER NOT NULL
);
"""


def conn():
    if not hasattr(_local, "c"):
        _local.c = sqlite3.connect(DB_PATH, timeout=10)
        _local.c.row_factory = sqlite3.Row
        _local.c.execute("PRAGMA journal_mode=WAL")
    return _local.c


def init(path):
    global DB_PATH
    DB_PATH = path
    conn().executescript(SCHEMA)
    conn().commit()


def apply_event(ev) -> bool:
    """Fold one normalized event into incident state.

    Returns False if this exact key was already applied — the duplicate case,
    which is expected traffic and not an error.
    """
    c = conn()
    try:
        c.execute(
            "INSERT INTO applied_keys (idempotency_key, applied_at) VALUES (?, ?)",
            (ev["idempotency_key"], ev["received_at"]),
        )
    except sqlite3.IntegrityError:
        return False

    existing = c.execute(
        "SELECT fingerprint, occurrences FROM incidents WHERE fingerprint = ?",
        (ev["fingerprint"],),
    ).fetchone()

    if existing:
        # A repeat of a known condition bumps the counter instead of creating
        # a second row. This is what stops a flapping host from producing 400
        # incidents overnight.
        c.execute(
            """UPDATE incidents
                  SET status = ?, last_seen = ?, occurrences = occurrences + 1,
                      severity = ?, description = ?
                WHERE fingerprint = ?""",
            (ev["status"], ev["received_at"], ev["severity"],
             ev["description"], ev["fingerprint"]),
        )
    else:
        c.execute(
            """INSERT INTO incidents
                 (fingerprint, title, severity, status, source, target,
                  description, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ev["fingerprint"], ev["title"], ev["severity"], ev["status"],
             ev["source"], ev["target"], ev["description"],
             ev["received_at"], ev["received_at"]),
        )

    c.commit()
    return True


def list_incidents():
    return [dict(r) for r in conn().execute(
        """SELECT * FROM incidents
            ORDER BY CASE status WHEN 'firing' THEN 0 ELSE 1 END,
                     CASE severity WHEN 'critical' THEN 0 WHEN 'error' THEN 1
                                   WHEN 'warning' THEN 2 ELSE 3 END,
                     last_seen DESC"""
    ).fetchall()]


def ack(fingerprint, who, when):
    conn().execute(
        "UPDATE incidents SET acked_by = ?, acked_at = ? WHERE fingerprint = ?",
        (who, when, fingerprint),
    )
    conn().commit()


def resolve(fingerprint, when):
    conn().execute(
        "UPDATE incidents SET status = 'resolved', last_seen = ? WHERE fingerprint = ?",
        (when, fingerprint),
    )
    conn().commit()
