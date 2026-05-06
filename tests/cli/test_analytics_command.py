"""Test the `devrel argus report` CLI verb."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app
from devrel_swarm.core.argus import PerformanceReport
from devrel_swarm.project.state import init_db

runner = CliRunner()


def _stub_report() -> PerformanceReport:
    return PerformanceReport(
        period_start=datetime(2026, 4, 25, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        top_performers=[],
        bottom_performers=[],
        trend_signals=["Stub trend"],
        recommendations=[],
        sources_ok={"posthog": True, "github": True, "instantly": True, "social": True},
    )


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Bootstrap a minimal .devrel/ in tmp_path with a real config so
    `find_devrel_root` recognizes it, and chdir into it."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / "deliverables").mkdir()
    (devrel / "config.toml").write_text('[project]\nname = "stub"\nurl = "https://example.com"\n')
    init_db(devrel / "state.db")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_analytics_report_writes_markdown_deliverable(project_dir):
    with patch("devrel_swarm.cli.argus._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["argus", "report", "--since", "7d"])

    assert result.exit_code == 0, result.stdout
    deliverables = list((project_dir / ".devrel" / "deliverables").glob("analytics-*.md"))
    assert len(deliverables) == 1
    assert "Argus Performance Report" in deliverables[0].read_text()


def test_analytics_report_json_format_emits_json(project_dir):
    with patch("devrel_swarm.cli.argus._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["argus", "report", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sources_ok"]["posthog"] is True


def test_analytics_report_push_calls_notification_service(project_dir):
    """--push flow should construct NotificationService and call telegram + email."""
    with (
        patch("devrel_swarm.cli.argus._build_argus") as build,
        patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls,
    ):
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock(return_value=True)
        svc.send_email = AsyncMock(return_value=True)
        svc.close = AsyncMock()

        result = runner.invoke(app, ["argus", "report", "--push"])

    assert result.exit_code == 0
    svc.send_telegram.assert_awaited_once()
    svc.send_email.assert_awaited_once()
    svc.close.assert_awaited_once()


def test_analytics_history_renders_metric_trajectory(project_dir):
    """history verb reads metric_history and produces a markdown table."""
    from devrel_swarm.project.state import open_db

    db = project_dir / ".devrel" / "state.db"
    with open_db(db) as conn:
        conn.executemany(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("blog/cli", "2026-04-18T00:00:00+00:00", 100.0, "page_views", "blog"),
                ("blog/cli", "2026-04-25T00:00:00+00:00", 150.0, "page_views", "blog"),
                ("blog/cli", "2026-05-02T00:00:00+00:00", 300.0, "page_views", "blog"),
            ],
        )
        conn.commit()

    result = runner.invoke(app, ["argus", "history", "blog/cli"])
    assert result.exit_code == 0
    assert "blog/cli" in result.stdout
    assert "2026-04-18" in result.stdout
    assert "+50.0%" in result.stdout  # delta from 100 to 150
    assert "+100.0%" in result.stdout  # delta from 150 to 300


def test_analytics_history_json_format(project_dir):
    from devrel_swarm.project.state import open_db

    db = project_dir / ".devrel" / "state.db"
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("blog/x", "2026-05-02T00:00:00+00:00", 42.0, "page_views", "blog"),
        )
        conn.commit()

    result = runner.invoke(app, ["argus", "history", "blog/x", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["primary_metric"] == 42.0


def test_analytics_history_unknown_content_id_exits_with_code_1(project_dir):
    result = runner.invoke(app, ["argus", "history", "blog/never"])
    assert result.exit_code == 1


def test_analytics_diff_shows_top_movers(project_dir):
    from devrel_swarm.project.state import open_db

    db = project_dir / ".devrel" / "state.db"
    with open_db(db) as conn:
        conn.executemany(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("blog/big-mover", "2026-04-25T00:00:00+00:00", 100.0, "page_views", "blog"),
                ("blog/big-mover", "2026-05-02T00:00:00+00:00", 500.0, "page_views", "blog"),
                ("blog/flat", "2026-04-25T00:00:00+00:00", 50.0, "page_views", "blog"),
                ("blog/flat", "2026-05-02T00:00:00+00:00", 51.0, "page_views", "blog"),
                ("blog/new", "2026-05-02T00:00:00+00:00", 200.0, "page_views", "blog"),
                ("blog/gone", "2026-04-25T00:00:00+00:00", 80.0, "page_views", "blog"),
            ],
        )
        conn.commit()

    result = runner.invoke(app, ["argus", "diff", "2026-04-25", "2026-05-02"])
    assert result.exit_code == 0
    # Big mover ranks first (largest absolute delta)
    out = result.stdout
    big_idx = out.find("blog/big-mover")
    flat_idx = out.find("blog/flat")
    assert big_idx >= 0 and flat_idx >= 0
    assert big_idx < flat_idx
    # New / gone classifications surfaced
    assert "new" in out
    assert "gone" in out


def test_analytics_diff_json_format(project_dir):
    from devrel_swarm.project.state import open_db

    db = project_dir / ".devrel" / "state.db"
    with open_db(db) as conn:
        conn.executemany(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("blog/x", "2026-04-25T00:00:00+00:00", 100.0, "page_views", "blog"),
                ("blog/x", "2026-05-02T00:00:00+00:00", 200.0, "page_views", "blog"),
            ],
        )
        conn.commit()

    result = runner.invoke(app, ["argus", "diff", "2026-04-25", "2026-05-02", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["content_id"] == "blog/x"
    assert payload[0]["delta_pct"] == 100.0


def test_analytics_calibration_scores_double_down_recs(project_dir):
    """Seed a double_down rec at period 1 + post-period metrics that grew →
    calibration must score it as panned_out."""
    from devrel_swarm.project.state import open_db

    db = project_dir / ".devrel" / "state.db"
    with open_db(db) as conn:
        # Seed a report row (FK target)
        conn.execute(
            "INSERT INTO analytics_reports (id, period_start, period_end, report_json) "
            "VALUES (1, ?, ?, ?)",
            ("2026-04-25T00:00:00+00:00", "2026-05-02T00:00:00+00:00", "{}"),
        )
        # Anchor metric at first_seen_period
        conn.execute(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("blog/winner", "2026-05-02T00:00:00+00:00", 100.0, "page_views", "blog"),
        )
        # Subsequent observation grew → double_down prediction held
        conn.execute(
            "INSERT INTO metric_history "
            "(content_id, period_end, primary_metric, metric_name, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("blog/winner", "2026-05-09T00:00:00+00:00", 200.0, "page_views", "blog"),
        )
        # The recommendation
        conn.execute(
            "INSERT INTO analytics_recommendations "
            "(report_id, period_end, action, target, target_type, rationale, "
            "confidence, source_ids_json, evidence_json, first_seen_period) "
            "VALUES (1, ?, 'double_down', 'theme:winner', 'theme', 'rationale', "
            "0.9, ?, '[]', ?)",
            ("2026-05-02T00:00:00+00:00", '["blog/winner"]', "2026-05-02T00:00:00+00:00"),
        )
        conn.commit()

    result = runner.invoke(app, ["argus", "calibration", "--format", "json"])
    assert result.exit_code == 0
    cal = json.loads(result.stdout)
    assert cal["scored_recs"] == 1
    assert cal["by_action"]["double_down"]["n"] == 1
    assert cal["by_action"]["double_down"]["panned_out"] == 1
    assert cal["by_action"]["double_down"]["rate"] == 1.0


def test_analytics_calibration_handles_empty_db(project_dir):
    result = runner.invoke(app, ["argus", "calibration"])
    assert result.exit_code == 0
    assert "0" in result.stdout  # scored count is 0
    assert "No scored recommendations" in result.stdout


def test_analytics_summary_aggregates_across_projects(tmp_path, monkeypatch):
    """Build two fake projects with state.db files; summary aggregates both."""
    from devrel_swarm.project.state import init_db, open_db

    proj_a = tmp_path / "project-a"
    (proj_a / ".devrel").mkdir(parents=True)
    init_db(proj_a / ".devrel" / "state.db")
    with open_db(proj_a / ".devrel" / "state.db") as conn:
        conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-04-25T00:00:00+00:00", "2026-05-02T00:00:00+00:00", "{}"),
        )
        conn.execute("INSERT INTO costs (agent, model, cost_usd) VALUES ('argus', 'sonnet', 0.03)")
        conn.commit()

    proj_b = tmp_path / "project-b"
    (proj_b / ".devrel").mkdir(parents=True)
    init_db(proj_b / ".devrel" / "state.db")
    # No reports in project-b — should still appear with totals=0

    # Cd somewhere outside; use --root flag explicitly
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["argus", "summary", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    projects = {p["project"]: p for p in payload}
    assert str(proj_a) in projects
    assert str(proj_b) in projects
    assert projects[str(proj_a)]["spend_usd"] == 0.03
    assert projects[str(proj_a)]["last_report"] == "2026-05-02T00:00:00+00:00"
    assert projects[str(proj_b)]["spend_usd"] == 0.0


def test_analytics_summary_empty_root_returns_zero_projects(tmp_path):
    """A root with no .devrel/ trees should produce an empty project list."""
    result = runner.invoke(app, ["argus", "summary", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_analytics_report_push_skipped_when_sources_partial(project_dir):
    """If any source failed, --push must be skipped by default."""
    partial = PerformanceReport(
        period_start=datetime(2026, 4, 25, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        top_performers=[],
        bottom_performers=[],
        trend_signals=[],
        recommendations=[],
        sources_ok={"posthog": True, "github": False, "instantly": True, "social": True},
    )
    with (
        patch("devrel_swarm.cli.argus._build_argus") as build,
        patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls,
    ):
        argus = build.return_value
        argus.run = AsyncMock(return_value=partial)
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock()
        svc.send_email = AsyncMock()
        svc.close = AsyncMock()

        result = runner.invoke(app, ["argus", "report", "--push"])

    assert result.exit_code == 0
    svc.send_telegram.assert_not_awaited()
    svc.send_email.assert_not_awaited()


def test_analytics_report_push_on_partial_overrides_gate(project_dir):
    """--push-on-partial bypasses the gate for ops use cases."""
    partial = PerformanceReport(
        period_start=datetime(2026, 4, 25, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        top_performers=[],
        bottom_performers=[],
        trend_signals=[],
        recommendations=[],
        sources_ok={"posthog": False, "github": True, "instantly": True, "social": True},
    )
    with (
        patch("devrel_swarm.cli.argus._build_argus") as build,
        patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls,
    ):
        argus = build.return_value
        argus.run = AsyncMock(return_value=partial)
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock(return_value=True)
        svc.send_email = AsyncMock(return_value=True)
        svc.close = AsyncMock()

        result = runner.invoke(app, ["argus", "report", "--push", "--push-on-partial"])

    assert result.exit_code == 0
    svc.send_telegram.assert_awaited_once()
    svc.send_email.assert_awaited_once()


def test_analytics_report_push_failure_does_not_crash(project_dir):
    """If push raises, exit code stays 0 and a warning is printed to stderr."""
    with (
        patch("devrel_swarm.cli.argus._build_argus") as build,
        patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls,
    ):
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock(side_effect=RuntimeError("telegram api 503"))
        svc.send_email = AsyncMock(return_value=True)
        svc.close = AsyncMock()

        result = runner.invoke(app, ["argus", "report", "--push"])

    assert result.exit_code == 0  # graceful — push failure must not break the verb
    svc.close.assert_awaited_once()  # finally: still ran
