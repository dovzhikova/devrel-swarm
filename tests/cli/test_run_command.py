"""Tests for `devrel run`."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def test_run_health_calls_watchdog(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_single_task = AsyncMock(return_value=MagicMock(success=True, agent="watchdog", result={"checks": "ok"}, error=None))
            result = runner.invoke(app, ["run", "--health"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        inst.run_single_task.assert_awaited_once_with("watchdog", "Check system health")
    finally:
        os.chdir(cwd)


def test_run_agent_dispatches_named_agent(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_single_task = AsyncMock(return_value=MagicMock(success=True, agent="kai", result="ok", error=None))
            result = runner.invoke(app, ["run", "--agent", "kai", "--task", "Write tutorial"],
                                   env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        called_agent, called_task = inst.run_single_task.await_args.args
        assert called_agent == "kai"
        assert called_task == "Write tutorial"
    finally:
        os.chdir(cwd)


def test_run_default_calls_weekly_cycle(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_weekly_cycle = AsyncMock(return_value=MagicMock(week_of="2026-W18"))
            result = runner.invoke(app, ["run"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        inst.run_weekly_cycle.assert_awaited_once()
    finally:
        os.chdir(cwd)


def test_run_fails_without_devrel_dir(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["run"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
