"""Project state DB: SQLite at .devrel/state.db.

Stores: jobs (kind, status, started/finished timestamps), costs (per-call
token + USD ledger consumed by BudgetGate), checkpoints (per-agent context
snapshots, one per (agent, week_of) pair).

In Phase 2 the DB is initialized empty by `devrel init`. Agents start
writing to it in Phase 3 (quality pipeline cost recording) and beyond.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    week_of TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, week_of)
);

CREATE TABLE IF NOT EXISTS analytics_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_reports_period
    ON analytics_reports(period_end);

CREATE TABLE IF NOT EXISTS metric_history (
    content_id TEXT NOT NULL,
    period_end TEXT NOT NULL,
    primary_metric REAL NOT NULL,
    metric_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    PRIMARY KEY (content_id, period_end)
);

CREATE INDEX IF NOT EXISTS idx_metric_history_content_period
    ON metric_history(content_id, period_end DESC);
"""


def init_db(db_path: Path) -> None:
    """Create the DB file and apply the schema. Idempotent — preserves
    existing data and bumps schema_meta to the current SCHEMA_VERSION."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (version, applied_at) "
            "VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        conn.commit()


def get_schema_version(db_path: Path) -> int | None:
    """Return the current schema version, or None if the DB is missing /
    has no schema_meta table."""
    if not db_path.is_file():
        return None
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
        except sqlite3.OperationalError:
            return None
        row = cur.fetchone()
        return row[0] if row else None


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection with row_factory set and
    foreign-key enforcement enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
