"""Tests for `devrel kb {add, list, refresh}` subcommands."""

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
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args)
    finally:
        os.chdir(cwd)


def test_kb_add_invokes_harvester(tmp_path):
    _init(tmp_path)
    fake_doc = MagicMock(filename="page.md", category="docs")
    with patch("devrel_swarm.cli.kb.KBHarvester") as M:
        inst = M.return_value
        inst.harvest_url = AsyncMock(return_value=fake_doc)
        inst.close = AsyncMock(return_value=None)
        r = _run(tmp_path, ["kb", "add", "https://example.com/x", "--category", "docs"])
    assert r.exit_code == 0, r.output
    inst.harvest_url.assert_awaited_once()
    args, kwargs = inst.harvest_url.await_args
    assert args[0] == "https://example.com/x"
    assert kwargs.get("category") == "docs"
    assert "page.md" in r.output


def test_kb_add_failure_exits_nonzero(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.kb.KBHarvester") as M:
        inst = M.return_value
        inst.harvest_url = AsyncMock(return_value=None)
        inst.close = AsyncMock(return_value=None)
        r = _run(tmp_path, ["kb", "add", "https://example.com/bad"])
    assert r.exit_code == 1, r.output
    assert "Failed" in r.output


def test_kb_list_shows_docs(tmp_path):
    _init(tmp_path)
    kb = tmp_path / ".devrel" / "kb" / "docs"
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "intro.md").write_text("# hi")
    (kb / "guide.md").write_text("# hi")
    r = _run(tmp_path, ["kb", "list"])
    assert r.exit_code == 0, r.output
    assert "intro.md" in r.output
    assert "guide.md" in r.output
    assert "2 doc" in r.output


def test_kb_refresh_calls_harvest_all(tmp_path):
    _init(tmp_path)
    report = {
        "harvested": 2,
        "failed": 1,
        "sources": [
            {"name": "a", "status": "ok", "file": "a.md"},
            {"name": "b", "status": "ok", "file": "b.md"},
            {"name": "c", "status": "failed", "error": "boom"},
        ],
    }
    with patch("devrel_swarm.cli.kb.KBHarvester") as M:
        inst = M.return_value
        inst.harvest_all = AsyncMock(return_value=report)
        inst.close = AsyncMock(return_value=None)
        r = _run(tmp_path, ["kb", "refresh"])
    assert r.exit_code == 0, r.output
    inst.harvest_all.assert_awaited_once()
    assert "harvested=2" in r.output
    assert "failed=1" in r.output
