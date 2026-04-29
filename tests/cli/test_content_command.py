"""Tests for `devrel content draft` and `devrel content audit`."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init_project(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content.run_pipeline")
def test_draft_writes_deliverable_and_trace(mock_pipeline, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_pipeline.return_value = MagicMock(
        final_text="Final clean draft.",
        flagged=False,
        stages=[],
        revision_trace={"content_type": "tutorial", "stages": []},
    )
    mock_pipeline.side_effect = None
    # Make run_pipeline awaitable
    async def _runner(**kwargs):
        return mock_pipeline.return_value
    import devrel_swarm.cli.content as content_mod
    content_mod.run_pipeline = AsyncMock(return_value=mock_pipeline.return_value)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("initial draft", None)))

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "content", "draft", "tutorial on feature flags",
                "--type", "tutorial",
            ],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    deliverables = list((tmp_path / ".devrel" / "deliverables").glob("*.md"))
    traces = list((tmp_path / ".devrel" / "deliverables").glob("*-trace.json"))
    assert len(deliverables) == 1
    assert len(traces) == 1
    assert "Final clean draft." in deliverables[0].read_text()
    trace = json.loads(traces[0].read_text())
    assert trace["content_type"] == "tutorial"


def test_draft_fails_without_project(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "x", "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0


def test_audit_runs_pipeline_against_existing_file(tmp_path):
    _init_project(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("This is a draft about feature flags.")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli.content.run_pipeline", new=AsyncMock(return_value=MagicMock(
            final_text="rewritten",
            flagged=False, stages=[],
            revision_trace={"content_type": "tutorial", "stages": []},
        ))):
            with patch("devrel_swarm.cli.content._build_llm_client", return_value=MagicMock(
                generate=AsyncMock(return_value=("x", None))
            )):
                result = runner.invoke(
                    app,
                    ["content", "audit", str(draft), "--type", "tutorial"],
                    env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
                )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "rewritten" in result.output or "rewritten" in (tmp_path / ".devrel" / "deliverables").glob("*.md").__iter__().__next__().read_text()
