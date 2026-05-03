"""Test the `devrel analytics report` CLI verb."""

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
    (devrel / "config.toml").write_text(
        '[project]\nname = "stub"\nurl = "https://example.com"\n'
    )
    init_db(devrel / "state.db")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_analytics_report_writes_markdown_deliverable(project_dir):
    with patch("devrel_swarm.cli.analytics._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["analytics", "report", "--since", "7d"])

    assert result.exit_code == 0, result.stdout
    deliverables = list((project_dir / ".devrel" / "deliverables").glob("analytics-*.md"))
    assert len(deliverables) == 1
    assert "Argus Performance Report" in deliverables[0].read_text()


def test_analytics_report_json_format_emits_json(project_dir):
    with patch("devrel_swarm.cli.analytics._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["analytics", "report", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sources_ok"]["posthog"] is True


def test_analytics_report_push_calls_notification_service(project_dir):
    """--push flow should construct NotificationService and call telegram + email."""
    with patch("devrel_swarm.cli.analytics._build_argus") as build, \
         patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock(return_value=True)
        svc.send_email = AsyncMock(return_value=True)
        svc.close = AsyncMock()

        result = runner.invoke(app, ["analytics", "report", "--push"])

    assert result.exit_code == 0
    svc.send_telegram.assert_awaited_once()
    svc.send_email.assert_awaited_once()
    svc.close.assert_awaited_once()


def test_analytics_report_push_failure_does_not_crash(project_dir):
    """If push raises, exit code stays 0 and a warning is printed to stderr."""
    with patch("devrel_swarm.cli.analytics._build_argus") as build, \
         patch("devrel_swarm.tools.notifications.NotificationService") as svc_cls:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        svc = svc_cls.return_value
        svc.send_telegram = AsyncMock(side_effect=RuntimeError("telegram api 503"))
        svc.send_email = AsyncMock(return_value=True)
        svc.close = AsyncMock()

        result = runner.invoke(app, ["analytics", "report", "--push"])

    assert result.exit_code == 0  # graceful — push failure must not break the verb
    svc.close.assert_awaited_once()  # finally: still ran
