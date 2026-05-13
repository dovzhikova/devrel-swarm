"""Tests for `devrel content draft` and `devrel content audit`."""

from __future__ import annotations

import asyncio
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
@patch("devrel_swarm.cli.content._build_kai")
def test_draft_writes_deliverable_and_trace(mock_build_kai, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="initial draft"))
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "tutorial on feature flags",
            "status": "generated",
            "content": "Final clean draft.",
            "grounding_sources": ["sdks/python.md"],
            "pain_points_addressed": ["init errors"],
            "real_issues_referenced": [101],
            "revision": {"strengths": ["clear"], "remaining_issues": []},
            "code_validation": {
                "total_blocks": 1,
                "validated": 1,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "all_passed": True,
                "errors": [],
            },
        }
    )
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "content",
                "draft",
                "tutorial on feature flags",
                "--type",
                "tutorial",
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
    assert trace["grounding_sources"] == ["sdks/python.md"]
    assert trace["agent"] == "kai"
    fake_kai.execute.assert_awaited_once_with(
        task="tutorial on feature flags", content_type="tutorial"
    )


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
        with patch(
            "devrel_swarm.cli.content.run_pipeline",
            new=AsyncMock(
                return_value=MagicMock(
                    final_text="rewritten",
                    flagged=False,
                    stages=[],
                    revision_trace={"content_type": "tutorial", "stages": []},
                )
            ),
        ):
            with patch(
                "devrel_swarm.cli.content._build_llm_client",
                return_value=MagicMock(generate=AsyncMock(return_value="x")),
            ):
                result = runner.invoke(
                    app,
                    ["content", "audit", str(draft), "--type", "tutorial"],
                    env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
                )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert (
        "rewritten" in result.output
        or "rewritten"
        in (tmp_path / ".devrel" / "deliverables").glob("*.md").__iter__().__next__().read_text()
    )


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


def test_build_llm_client_raises_without_api_key(monkeypatch, tmp_path):
    from devrel_swarm.cli import content as content_mod
    from devrel_swarm.project.paths import ProjectPaths

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(typer.Exit):
        content_mod._build_llm_client(ProjectPaths.from_root(tmp_path))


@patch("devrel_swarm.cli.content._build_kai")
def test_draft_loads_llm_key_from_project_env(mock_build_kai, monkeypatch, tmp_path):
    _init_project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".devrel" / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-from-file\n")
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "topic",
            "status": "generated",
            "content": "Final.",
            "grounding_sources": ["sdks/python.md"],
            "code_validation": {"all_passed": True, "failed": 0, "validated": 0},
        }
    )
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["content", "draft", "topic", "--type", "tutorial"])
    finally:
        os.chdir(cwd)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY is required" not in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content._build_kai")
def test_draft_times_out_cleanly(mock_build_kai, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="draft"))
    fake_kai = MagicMock()

    async def slow_execute(**_kwargs):
        await asyncio.sleep(60)

    fake_kai.execute = slow_execute
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "topic", "--type", "tutorial", "--timeout", "0.1"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)

    assert result.exit_code == 1
    assert "Kai timed out after 0.1s" in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content._build_kai")
def test_draft_exits_nonzero_when_kai_blocks(mock_build_kai, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="draft"))
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "topic",
            "status": "blocked_by_quality_gate",
            "content": "",
            "error": "slop persists",
        }
    )
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "topic", "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 1
    assert "blocked_by_quality_gate" in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content._build_kai")
def test_draft_warns_when_no_kb_grounding(mock_build_kai, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="draft"))
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "topic",
            "status": "generated",
            "content": "Final.",
            "grounding_sources": [],
            "code_validation": {
                "total_blocks": 0,
                "validated": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "all_passed": True,
                "errors": [],
            },
        }
    )
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "topic", "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "No KB sources matched" in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content._build_kai")
def test_draft_warns_when_code_validation_fails(mock_build_kai, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="draft"))
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "topic",
            "status": "generated",
            "content": "Final.",
            "grounding_sources": ["sdks/python.md"],
            "code_validation": {
                "total_blocks": 3,
                "validated": 3,
                "passed": 1,
                "failed": 2,
                "skipped": 0,
                "all_passed": False,
                "errors": [],
            },
        }
    )
    mock_build_kai.return_value = fake_kai

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "topic", "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "Code validation: 2/3 blocks failed" in result.output


@patch("devrel_swarm.cli.content._build_llm_client")
def test_audit_aborts_loud_exits_nonzero(mock_client, tmp_path):
    from devrel_swarm.quality.editorial import AbortLoud

    _init_project(tmp_path)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="x"))
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
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="x"))
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
