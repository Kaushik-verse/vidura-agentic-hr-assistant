"""SQLite persistence: connection handling and schema."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    job_title   TEXT NOT NULL,
    department  TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS leaves (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    leave_type   TEXT NOT NULL CHECK (leave_type IN ('annual', 'sick', 'unpaid')),
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    days         INTEGER NOT NULL CHECK (days > 0),
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'Pending'
                 CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
    decided_by   TEXT,
    decided_at   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by   TEXT NOT NULL,
    employee_id  INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    category     TEXT NOT NULL CHECK (category IN ('payroll', 'technical', 'leave', 'onboarding', 'benefits', 'other')),
    priority     TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'Open'
                 CHECK (status IN ('Open', 'In Progress', 'Resolved', 'Closed')),
    onboarding_task INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS interviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_name  TEXT NOT NULL,
    position        TEXT NOT NULL,
    interviewer     TEXT NOT NULL,
    scheduled_at    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'Scheduled'
                    CHECK (status IN ('Scheduled', 'Completed', 'Cancelled', 'No-show')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS performance_reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    reviewer     TEXT NOT NULL,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_leaves_employee ON leaves(employee_id);
CREATE INDEX IF NOT EXISTS idx_leaves_dates ON leaves(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""

TABLES = ("audit_log", "performance_reviews", "interviews", "tickets", "leaves", "employees")


def _db_path() -> Path:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection that commits on success and rolls back on error."""
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def init_db() -> None:
    """Create tables if needed and, when enabled and empty, load demo data."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        empty = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
    if empty and get_settings().seed_demo_data:
        from .demo_data import seed

        seed()
        log.info("Seeded demo data into %s", get_settings().db_path)


def reset_db() -> None:
    """Drop every table and recreate the schema (and demo data, if enabled)."""
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    log.warning("Database reset")
    init_db()
