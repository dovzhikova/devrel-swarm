"""Tests for `devrel docs build` and `devrel video record`."""

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
            return_value=MagicMock(success=True, agent="?", result="ok", error=None)
        )
        yield inst


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_docs_build_dispatches_to_dex(tmp_path, mock_atlas):
    _init(tmp_path)
    r = _run(tmp_path, ["docs", "build"])
    assert r.exit_code == 0, r.output
    agent, _ = mock_atlas.run_single_task.await_args.args
    assert agent == "dex"


def test_video_record_dispatches_to_vox(tmp_path, mock_atlas):
    _init(tmp_path)
    r = _run(tmp_path, ["video", "record", "Tutorial on widgets"])
    assert r.exit_code == 0, r.output
    agent, task = mock_atlas.run_single_task.await_args.args
    assert agent == "vox"
    assert "widgets" in task
