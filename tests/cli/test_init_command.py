"""Tests for `devrel init`."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _run_in(tmp_path, *args):
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)


def test_init_non_interactive_minimal(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "openclaw",
        "--url", "",
        "--github-repo", "",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".devrel" / "config.toml").is_file()
    assert (tmp_path / ".devrel" / "voice.md").is_file()
    assert (tmp_path / ".devrel" / "state.db").is_file()


def test_init_non_interactive_full(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "openclaw",
        "--url", "https://openclaw.ai",
        "--github-repo", "openclaw/openclaw",
    )
    assert result.exit_code == 0, result.output
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    assert 'name = "openclaw"' in body
    assert 'url = "https://openclaw.ai"' in body
    assert 'github_repo = "openclaw/openclaw"' in body


def test_init_dry_run_writes_nothing(tmp_path):
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name", "x",
        "--url", "",
        "--github-repo", "",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert not (tmp_path / ".devrel").exists()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "devrel-swarm" in result.output
