"""Tests for marketing subcommands (blog, landing, social, campaign)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_origin.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""]
        )
    finally:
        os.chdir(cwd)


@pytest.fixture
def mock_atlas():
    with patch("devrel_origin.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(
            return_value=MagicMock(
                success=True,
                agent="?",
                result={"k": "v"},
                error=None,
            )
        )
        yield M, inst


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_marketing_blog_dispatches_to_mox(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["marketing", "blog", "feature flags"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "mox"
    assert "blog post" in task.lower() and "feature flags" in task


def test_marketing_landing_dispatches_to_mox(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["marketing", "landing", "pricing v2"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "mox"
    assert "landing page" in task.lower() and "pricing v2" in task


def test_marketing_social_dispatches_to_mox(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["marketing", "social", "launch week"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "mox"
    assert "social batch" in task.lower() and "launch week" in task


def test_marketing_campaign_dispatches_to_mox(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["marketing", "campaign", "Q4 GTM push"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "mox"
    assert "campaign" in task.lower() and "Q4 GTM push" in task
