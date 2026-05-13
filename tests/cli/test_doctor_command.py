"""Tests for `devrel doctor`."""

from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from devrel_origin.cli import app

runner = CliRunner()


def _run_in(tmp_path, *args, env=None):
    cwd = os.getcwd()
    saved = os.environ.copy()
    try:
        os.chdir(tmp_path)
        if env:
            os.environ.update(env)
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(saved)


def _init(tmp_path):
    runner.invoke(
        app,
        [
            "init",
            "--non-interactive",
            "--name",
            "x",
            "--url",
            "",
            "--github-repo",
            "",
        ],
    )


def test_doctor_fails_when_no_devrel(tmp_path):
    result = _run_in(tmp_path, "doctor")
    assert result.exit_code != 0
    assert "No .devrel/" in result.output or "not found" in result.output.lower()


def test_doctor_passes_with_anthropic_key(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    result = _run_in(tmp_path, "doctor", env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert result.exit_code == 0, result.output
    # Doctor now reports the LLM key check as `llm_api_key` (one-of Anthropic
    # or OpenRouter) and the detail line names which keys are set.
    assert "llm_api_key" in result.output
    assert "ANTHROPIC_API_KEY" in result.output


def test_doctor_fails_without_required_env(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    saved_a = os.environ.pop("ANTHROPIC_API_KEY", None)
    saved_or = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        result = _run_in(tmp_path, "doctor")
        assert result.exit_code != 0
        assert "llm_api_key" in result.output
        # Detail line points the user at the fix verb instead of just stating
        # the failure.
        assert "devrel auth" in result.output
    finally:
        if saved_a is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_a
        if saved_or is not None:
            os.environ["OPENROUTER_API_KEY"] = saved_or


def test_doctor_passes_with_only_openrouter_key(tmp_path):
    """OpenRouter alone is sufficient; doctor should pass on llm_api_key."""
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    saved_a = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        result = _run_in(tmp_path, "doctor", env={"OPENROUTER_API_KEY": "sk-or-test"})
        assert result.exit_code == 0, result.output
        assert "llm_api_key" in result.output
        assert "OPENROUTER_API_KEY" in result.output
    finally:
        if saved_a is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_a


def test_doctor_json_mode(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)
    result = _run_in(tmp_path, "doctor", "--json", env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] in ("ok", "warn", "fail")
    assert "checks" in data
    assert any(c["name"] == "llm_api_key" for c in data["checks"])
