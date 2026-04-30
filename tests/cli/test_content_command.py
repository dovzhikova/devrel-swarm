"""Tests for `devrel content draft` and `devrel content audit`."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
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


def test_audit_fails_without_project(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text("hello")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "audit", str(draft), "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0


def test_build_llm_client_raises_without_api_key(monkeypatch):
    from devrel_swarm.cli import content as content_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(typer.BadParameter):
        content_mod._build_llm_client()


@patch("devrel_swarm.cli.content._build_llm_client")
def test_draft_aborts_loud_exits_nonzero(mock_client, tmp_path):
    from devrel_swarm.quality.editorial import AbortLoud

    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("draft", None)))

    async def _raise(**kwargs):
        raise AbortLoud("slop persists")

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli.content.run_pipeline", new=_raise):
            result = runner.invoke(
                app,
                ["content", "draft", "topic", "--type", "tutorial"],
                env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 1


@patch("devrel_swarm.cli.content._build_llm_client")
def test_draft_flagged_prints_warning(mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("draft", None)))
    flagged_result = MagicMock(
        final_text="Final.",
        flagged=True,
        stages=[],
        revision_trace={"content_type": "tutorial", "stages": []},
    )

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch(
            "devrel_swarm.cli.content.run_pipeline",
            new=AsyncMock(return_value=flagged_result),
        ):
            result = runner.invoke(
                app,
                ["content", "draft", "topic", "--type", "tutorial"],
                env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "Flagged" in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
def test_audit_aborts_loud_exits_nonzero(mock_client, tmp_path):
    from devrel_swarm.quality.editorial import AbortLoud

    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("x", None)))
    draft = tmp_path / "draft.md"
    draft.write_text("seed")

    async def _raise(**kwargs):
        raise AbortLoud("nope")

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli.content.run_pipeline", new=_raise):
            result = runner.invoke(
                app,
                ["content", "audit", str(draft), "--type", "tutorial"],
                env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 1


@patch("devrel_swarm.cli.content._build_llm_client")
def test_audit_flagged_prints_warning(mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("x", None)))
    draft = tmp_path / "draft.md"
    draft.write_text("seed")
    flagged_result = MagicMock(
        final_text="Cleaned.",
        flagged=True,
        stages=[],
        revision_trace={"content_type": "tutorial", "stages": []},
    )

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch(
            "devrel_swarm.cli.content.run_pipeline",
            new=AsyncMock(return_value=flagged_result),
        ):
            result = runner.invoke(
                app,
                ["content", "audit", str(draft), "--type", "tutorial"],
                env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "Flagged" in result.output


def test_slug_helper_handles_special_chars():
    from devrel_swarm.cli.content import _slug

    assert _slug("Hello, World!") == "hello-world"
    assert _slug("@#$%^&*") == "draft"
    assert _slug("a" * 100) == "a" * 48
