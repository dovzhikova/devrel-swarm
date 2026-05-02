"""Migration: schema v1 -> v2 adds analytics_reports table for Argus."""

from __future__ import annotations

import sqlite3

from devrel_swarm.project.state import (
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
    open_db,
)


def test_schema_version_is_2():
    assert SCHEMA_VERSION == 2


def test_init_db_creates_analytics_reports_on_fresh_db(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_reports'"
        )
        assert cur.fetchone() is not None


def test_init_db_is_idempotent_on_existing_v1_db(tmp_path):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE schema_meta (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.execute(
            "INSERT INTO schema_meta (version, applied_at) VALUES (1, datetime('now'))"
        )
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, "
            "started_at TEXT, finished_at TEXT, error TEXT)"
        )
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES ('job-1', 'run', 'completed')"
        )
        conn.commit()

    init_db(db)

    assert get_schema_version(db) == 2
    with open_db(db) as conn:
        rows = conn.execute("SELECT id FROM jobs").fetchall()
        assert [r["id"] for r in rows] == ["job-1"]
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_reports'"
        )
        assert cur.fetchone() is not None


def test_can_insert_and_read_analytics_report(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z", '{"foo": "bar"}'),
        )
        conn.commit()
        row = conn.execute(
            "SELECT report_json FROM analytics_reports WHERE id = 1"
        ).fetchone()
        assert row["report_json"] == '{"foo": "bar"}'
