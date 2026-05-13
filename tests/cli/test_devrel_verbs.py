"""Tests for devrel-pipeline verbs (triage, listen, synthesize, experiment)."""

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
                result={"summary": "ok"},
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


def test_triage_dispatches_to_sage(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["triage", "--days", "3"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "sage"
    assert "3 days" in task


def test_listen_dispatches_to_echo(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["listen", "--platforms", "reddit"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "echo"
    assert "reddit" in task


def test_synthesize_dispatches_to_iris(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["synthesize"])
    assert result.exit_code == 0, result.output
    agent, _ = inst.run_single_task.await_args.args
    assert agent == "iris"


def test_experiment_dispatches_to_nova(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["experiment", "Bigger CTA increases signups"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "nova"
    assert "Bigger CTA" in task


def test_json_output_emits_valid_json(tmp_path, mock_atlas):
    _init(tmp_path)
    result = _run(tmp_path, ["triage", "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["agent"] == "?"  # mock returned this
    assert data["success"] is True
