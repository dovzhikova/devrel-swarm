"""Tests for `devrel auth`."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from devrel_origin.cli import app

runner = CliRunner()


def _init(tmp_path: Path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


def _read_env(env_file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_file.is_file():
        return out
    for line in env_file.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def test_auth_writes_anthropic_key_with_chmod_600(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "anthropic",
                "--key",
                "sk-ant-test-1234",
                "--no-validate",
            ],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    env_file = tmp_path / ".devrel" / ".env"
    assert env_file.is_file()
    env = _read_env(env_file)
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-test-1234"
    # POSIX chmod 600. Skip the assertion on Windows (CI matrix).
    if os.name == "posix":
        mode = env_file.stat().st_mode & 0o777
        assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_auth_writes_openrouter_key(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "openrouter",
                "--key",
                "sk-or-test",
                "--no-validate",
            ],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    env = _read_env(tmp_path / ".devrel" / ".env")
    assert env.get("OPENROUTER_API_KEY") == "sk-or-test"


def test_auth_blocks_overwrite_without_rotate(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "anthropic",
                "--key",
                "sk-ant-original",
                "--no-validate",
            ],
        )
        # Second run without --rotate; --non-interactive so it fails fast
        # rather than waiting for stdin input that never comes.
        result = runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "anthropic",
                "--no-validate",
                "--non-interactive",
            ],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
    # Original key untouched.
    env = _read_env(tmp_path / ".devrel" / ".env")
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-original"


def test_auth_rotate_overwrites_existing(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["auth", "--provider", "anthropic", "--key", "old-key", "--no-validate"],
        )
        result = runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "anthropic",
                "--key",
                "new-key",
                "--no-validate",
                "--rotate",
            ],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    env = _read_env(tmp_path / ".devrel" / ".env")
    assert env.get("ANTHROPIC_API_KEY") == "new-key"
    assert "rotated" in result.output


def test_auth_non_interactive_requires_key(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["auth", "--provider", "anthropic", "--no-validate", "--non-interactive"],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
    assert "--key" in result.output


def test_auth_rejects_unknown_provider(tmp_path: Path) -> None:
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["auth", "--provider", "bogus", "--key", "x", "--no-validate"],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0


def test_auth_validation_success_writes_key(tmp_path: Path) -> None:
    """The default validate path writes the key when the ping returns ok."""
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch(
            "devrel_origin.cli.auth._validate",
            new=AsyncMock(return_value=(True, "")),
        ):
            result = runner.invoke(
                app,
                ["auth", "--provider", "anthropic", "--key", "sk-validated"],
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "key validated" in result.output
    env = _read_env(tmp_path / ".devrel" / ".env")
    assert env.get("ANTHROPIC_API_KEY") == "sk-validated"


def test_auth_validation_failure_blocks_write(tmp_path: Path) -> None:
    """Validation failure must NOT write the key; surfaces the upstream error."""
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch(
            "devrel_origin.cli.auth._validate",
            new=AsyncMock(return_value=(False, "401 Unauthorized")),
        ):
            result = runner.invoke(
                app,
                ["auth", "--provider", "anthropic", "--key", "sk-bad"],
            )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
    assert "401 Unauthorized" in result.output
    env_file = tmp_path / ".devrel" / ".env"
    if env_file.is_file():
        env = _read_env(env_file)
        assert env.get("ANTHROPIC_API_KEY") != "sk-bad"


def test_auth_masks_key_in_output(tmp_path: Path) -> None:
    """Success message must not echo the full key (history / log hygiene)."""
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "auth",
                "--provider",
                "anthropic",
                "--key",
                "sk-ant-secret-payload-12345",
                "--no-validate",
            ],
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "sk-ant-secret-payload-12345" not in result.output
    # Masked form: prefix + "..." + suffix
    assert "..." in result.output
