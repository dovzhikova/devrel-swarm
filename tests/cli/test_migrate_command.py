"""Tests for `devrel migrate`."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from devrel_origin.cli import app
from devrel_origin.project.state import SCHEMA_VERSION

runner = CliRunner()


def _init_project(tmp_path: Path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


def _make_v4_state_db(db_path: Path) -> None:
    """Synthesize a state.db at the pre-v5 shape for migration tests.

    The v4 layout pre-dated pillar/target_kind columns on
    analytics_recommendations. Skipping those columns proves the v5 ALTERs
    actually run.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER PRIMARY KEY,
                                      applied_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO schema_meta(version) VALUES (4);
            CREATE TABLE analytics_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE analytics_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                period_end TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                target_type TEXT NOT NULL,
                rationale TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_ids_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                first_seen_period TEXT NOT NULL,
                applied_at TEXT
            );
            """
        )
        conn.commit()


def test_migrate_upgrades_v4_to_v5(tmp_path: Path) -> None:
    _init_project(tmp_path)
    db = tmp_path / ".devrel" / "state.db"
    db.unlink(missing_ok=True)
    _make_v4_state_db(db)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["migrate"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "v4" in result.output
    assert f"v{SCHEMA_VERSION}" in result.output

    # pillar/target_kind columns now exist on analytics_recommendations.
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(analytics_recommendations)")}
    assert "pillar" in cols
    assert "target_kind" in cols


def test_migrate_idempotent_at_current_version(tmp_path: Path) -> None:
    _init_project(tmp_path)  # init_db already ran, schema is at SCHEMA_VERSION
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["migrate"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "already at schema" in result.output


def test_migrate_creates_db_when_missing(tmp_path: Path) -> None:
    _init_project(tmp_path)
    db = tmp_path / ".devrel" / "state.db"
    db.unlink(missing_ok=True)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["migrate"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert db.is_file()
    assert "created" in result.output


def test_migrate_fails_outside_devrel_project(tmp_path: Path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["migrate"])
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
