"""Tests for project state DB initialization and schema introspection."""

from __future__ import annotations

import sqlite3

import pytest

from devrel_swarm.project.state import (
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
    open_db,
)


def test_init_db_creates_file_and_tables(tmp_path):
    db = tmp_path / "state.db"
    assert not db.exists()
    init_db(db)
    assert db.is_file()
    with sqlite3.connect(db) as conn:
        names = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert names == [
        "analytics_recommendations",
        "analytics_reports",
        "checkpoints",
        "costs",
        "cro_funnel_metrics",
        "geo_visibility",
        "jobs",
        "metric_history",
        "schema_meta",
        "seo_keyword_metrics",
        "seo_page_profiles",
    ]


def test_init_db_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "dir" / "state.db"
    init_db(db)
    assert db.is_file()


def test_get_schema_version_returns_current(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    assert get_schema_version(db) == SCHEMA_VERSION


def test_get_schema_version_returns_none_when_db_missing(tmp_path):
    assert get_schema_version(tmp_path / "absent.db") is None


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    # Insert a row, then re-init; row must survive.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
            ("abc", "weekly_cycle", "queued"),
        )
        conn.commit()
    init_db(db)
    with sqlite3.connect(db) as conn:
        rows = list(conn.execute("SELECT id FROM jobs"))
    assert rows == [("abc",)]


def test_open_db_yields_row_factory_connection(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
            ("xyz", "triage", "queued"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", ("xyz",)).fetchone()
    assert row["id"] == "xyz"
    assert row["kind"] == "triage"
    assert row["status"] == "queued"


def test_jobs_status_check_constraint_enforced(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (id, kind, status) VALUES (?, ?, ?)",
                ("bad", "x", "garbage_status"),
            )
            conn.commit()
