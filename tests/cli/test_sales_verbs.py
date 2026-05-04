"""Tests for intel + sales subcommands."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

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
    with patch("devrel_swarm.cli._common.Atlas") as M:
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


def test_intel_dispatches_to_rex(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["intel", "AcmeCorp"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "rex"
    assert "AcmeCorp" in task


def test_sales_outreach_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "outreach", "Globex"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "Globex" in task and "outreach" in task.lower()


def test_sales_battlecard_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "battlecard", "Acme"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "battle card" in task.lower() and "Acme" in task


def test_sales_sequence_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "sequence", "Q3 launch"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "Q3 launch" in task
