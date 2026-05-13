"""Tests for `devrel docs build` and `devrel video record`."""

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


def test_docs_build_persists_dex_output_to_deliverables(tmp_path):
    """Dex's architecture / API / summary outputs must land on disk in
    .devrel/deliverables/dex-*.md so users don't have to pipe --json and split
    the blob themselves."""
    _init(tmp_path)
    fake_output = {
        "architecture_doc": "# Architecture\n\nLong markdown body.\n",
        "api_reference": "# API\n\nDetailed reference.\n",
        "llm_summary": "Three paragraph summary.",
        "modules": [{"path": "src/x.py", "language": "Python", "line_count": 10, "symbols": 1}],
        "languages": {"Python": 100.0},
        "status": "generated",
    }
    with patch("devrel_origin.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(
            return_value=MagicMock(success=True, agent="dex", output=fake_output, error=None)
        )
        r = _run(tmp_path, ["docs", "build"])
    assert r.exit_code == 0, r.output

    deliverables = tmp_path / ".devrel" / "deliverables"
    arch = deliverables / "dex-architecture.md"
    api = deliverables / "dex-api-reference.md"
    summary = deliverables / "dex-summary.md"
    modules = deliverables / "dex-modules.json"
    assert arch.is_file() and arch.read_text().startswith("# Architecture")
    assert api.is_file() and api.read_text().startswith("# API")
    assert summary.is_file() and summary.read_text().startswith("Three paragraph")
    assert modules.is_file()


def test_docs_build_skips_empty_fields(tmp_path):
    """Missing optional fields (e.g. llm_summary when no LLM client) should not
    create empty files."""
    _init(tmp_path)
    fake_output = {
        "architecture_doc": "# Arch\n",
        "api_reference": "# API\n",
        "modules": [],  # falsy, should skip dex-modules.json
        "status": "generated",
    }
    with patch("devrel_origin.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(
            return_value=MagicMock(success=True, agent="dex", output=fake_output, error=None)
        )
        r = _run(tmp_path, ["docs", "build"])
    assert r.exit_code == 0, r.output
    deliverables = tmp_path / ".devrel" / "deliverables"
    assert (deliverables / "dex-architecture.md").is_file()
    assert (deliverables / "dex-api-reference.md").is_file()
    assert not (deliverables / "dex-summary.md").exists()
    assert not (deliverables / "dex-modules.json").exists()


def test_docs_build_does_not_persist_on_failure(tmp_path):
    """A failed Dex run should not leave half-written deliverables."""
    _init(tmp_path)
    with patch("devrel_origin.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(
            return_value=MagicMock(success=False, agent="dex", output=None, error="parse failed")
        )
        r = _run(tmp_path, ["docs", "build"])
    assert r.exit_code != 0
    deliverables = tmp_path / ".devrel" / "deliverables"
    assert not (deliverables / "dex-architecture.md").exists()


def test_video_record_dispatches_to_vox(tmp_path, mock_atlas):
    _init(tmp_path)
    r = _run(tmp_path, ["video", "record", "Tutorial on widgets"])
    assert r.exit_code == 0, r.output
    agent, task = mock_atlas.run_single_task.await_args.args
    assert agent == "vox"
    assert "widgets" in task
