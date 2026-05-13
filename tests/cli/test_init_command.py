"""Tests for `devrel init`."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from devrel_origin.cli import app

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
        "--name",
        "openclaw",
        "--url",
        "",
        "--github-repo",
        "",
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
        "--name",
        "openclaw",
        "--url",
        "https://openclaw.ai",
        "--github-repo",
        "openclaw/openclaw",
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
        "--name",
        "x",
        "--url",
        "",
        "--github-repo",
        "",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert not (tmp_path / ".devrel").exists()


def test_init_success_points_at_devrel_auth(tmp_path):
    """First step a new user sees should be `devrel auth`, not just `devrel doctor`,
    so they don't bounce off the missing-key error before configuring."""
    result = _run_in(
        tmp_path,
        "init",
        "--non-interactive",
        "--name",
        "openclaw",
        "--url",
        "",
        "--github-repo",
        "",
    )
    assert result.exit_code == 0, result.output
    assert "devrel auth" in result.output
    assert "openrouter.ai" in result.output


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "devrel-origin" in result.output


# --- Onboarding chain ------------------------------------------------------
#
# `devrel init` (interactive, default) chains through auth -> doctor -> voice
# edit -> first draft. The chain is the new onboarding flow; verify (a) the
# scaffold-only escape hatches still work, (b) the chain runs in the right
# order, and (c) a key already configured short-circuits the auth step so
# re-running init doesn't ask again.


def test_init_skip_chain_prints_manual_next_steps(tmp_path):
    """With --skip-chain, scaffold runs and the manual next-steps block prints
    (mirrors the --non-interactive scaffold-only path)."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # --skip-chain is interactive-only; have to feed prompts.
        result = runner.invoke(
            app,
            ["init", "--skip-chain"],
            input="testproj\n\n\n",  # name, url, github_repo
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".devrel" / "config.toml").is_file()
    # Manual next-steps block.
    assert "devrel auth" in result.output
    assert "devrel doctor" in result.output
    assert "devrel content draft" in result.output
    # Chain didn't fire.
    assert "Step 1 of 4" not in result.output


def test_init_chain_aborts_when_user_declines_auth(tmp_path):
    """If the user types 'n' at the auth confirmation, the chain stops and
    no validation / draft is attempted."""
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # Prompts: name, url, github_repo, then "Configure your LLM key now?"
        # Decline the chain at the auth step.
        result = runner.invoke(
            app,
            ["init"],
            input="testproj\n\n\nn\n",
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "Step 1 of 4" in result.output
    assert "Skipping" in result.output
    # Chain stopped at auth; doctor/voice/draft never ran.
    assert "Step 2 of 4" not in result.output


def test_init_chain_skips_auth_when_key_already_present(tmp_path):
    """If .devrel/.env already has an API key (e.g. user re-runs init after
    auth), the auth step short-circuits with the 'already configured' message
    and the chain continues to doctor."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-test\n")

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # name, url, github_repo, then decline doctor's continue-on-fail prompt
        # if it triggers (unlikely on a fresh scaffold), then decline voice
        # edit, then decline draft.
        result = runner.invoke(
            app,
            ["init", "--skip-draft"],
            input="testproj\n\n\nn\nn\n",
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY already configured" in result.output
    # Auth was short-circuited, so doctor must have run.
    assert "Step 2 of 4" in result.output


@patch("devrel_origin.cli.init.subprocess.run")
def test_init_chain_skip_draft_stops_before_llm_call(mock_subproc, tmp_path):
    """--skip-draft runs auth + doctor + voice edit but does NOT call the LLM."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-test\n")

    with patch("devrel_origin.cli.content._build_kai") as mock_build_kai:
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # name, url, github_repo, decline doctor-continue (won't trigger
            # on pass), accept voice edit (which will mock subprocess)
            result = runner.invoke(
                app,
                ["init", "--skip-draft"],
                input="testproj\n\n\ny\n",
            )
        finally:
            os.chdir(cwd)
        assert result.exit_code == 0, result.output
        assert "Skipped first draft" in result.output
        # Editor invoked. Note subprocess.run also fires for `git remote get-url`
        # during the github_repo auto-detection in init_command, so we assert the
        # editor call specifically rather than total call count.
        editor_calls = [
            c
            for c in mock_subproc.call_args_list
            if c.args and c.args[0] and c.args[0][-1].endswith("voice.md")
        ]
        assert len(editor_calls) == 1, mock_subproc.call_args_list
        # Kai never built (no LLM call).
        mock_build_kai.assert_not_called()


# --- Helpers added in v0.2.13 (real-user-feedback fixes) ------------------
#
# - github_repo auto-detect from `git remote get-url origin` so the wizard
#   doesn't ask for what it can read.
# - editor fallback prefers nano/micro/code over vi (vi-trap is a known
#   onboarding killer).
# - content type is a numbered picker, not a free-text typo magnet
#   (real user typed "bblog_post" in 2026-05-13 testing).


def test_detect_github_repo_parses_https_url(tmp_path, monkeypatch):
    from devrel_origin.cli.init import _detect_github_repo

    monkeypatch.chdir(tmp_path)
    with patch("devrel_origin.cli.init.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/dovzhikova/devrel-origin.git\n",
        )
        assert _detect_github_repo() == "dovzhikova/devrel-origin"


def test_detect_github_repo_parses_ssh_url(tmp_path, monkeypatch):
    from devrel_origin.cli.init import _detect_github_repo

    monkeypatch.chdir(tmp_path)
    with patch("devrel_origin.cli.init.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="git@github.com:owner/repo.git\n")
        assert _detect_github_repo() == "owner/repo"


def test_detect_github_repo_returns_empty_for_non_github(tmp_path, monkeypatch):
    from devrel_origin.cli.init import _detect_github_repo

    monkeypatch.chdir(tmp_path)
    with patch("devrel_origin.cli.init.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="git@gitlab.com:owner/repo.git\n")
        assert _detect_github_repo() == ""


def test_detect_github_repo_returns_empty_when_not_a_repo(tmp_path, monkeypatch):
    from devrel_origin.cli.init import _detect_github_repo

    monkeypatch.chdir(tmp_path)
    with patch("devrel_origin.cli.init.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        assert _detect_github_repo() == ""


def test_pick_editor_prefers_visual_then_editor(monkeypatch):
    from devrel_origin.cli.init import _pick_editor

    monkeypatch.setenv("VISUAL", "code")
    monkeypatch.setenv("EDITOR", "vim")
    assert _pick_editor() == "code"
    monkeypatch.delenv("VISUAL")
    assert _pick_editor() == "vim"


def test_pick_editor_falls_back_to_friendly_when_env_unset(monkeypatch):
    from devrel_origin.cli.init import _pick_editor

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    # Force `nano` discoverable — common case on macOS / Debian / Ubuntu.
    with patch(
        "devrel_origin.cli.init.shutil.which",
        side_effect=lambda c: "/usr/bin/nano" if c == "nano" else None,
    ):
        assert _pick_editor() == "nano"


def test_pick_editor_falls_back_to_vi_as_last_resort(monkeypatch):
    from devrel_origin.cli.init import _pick_editor

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    with patch("devrel_origin.cli.init.shutil.which", return_value=None):
        assert _pick_editor() == "vi"


def test_pick_content_type_accepts_number():
    import devrel_origin.cli.init as init_mod

    with patch.object(init_mod.typer, "prompt", return_value="2"):
        assert init_mod._pick_content_type() == "blog_post"


def test_pick_content_type_accepts_name():
    import devrel_origin.cli.init as init_mod

    with patch.object(init_mod.typer, "prompt", return_value="cold_email"):
        assert init_mod._pick_content_type() == "cold_email"


def test_pick_content_type_rejects_typo_and_reprompts():
    """Real bug from 2026-05-13 testing: 'bblog_post' typo got past free-text
    prompt and silently flowed into Kai as the content type. Numbered picker
    rejects bad input and re-asks until valid."""
    import devrel_origin.cli.init as init_mod

    with patch.object(init_mod.typer, "prompt", side_effect=["bblog_post", "2"]):
        assert init_mod._pick_content_type() == "blog_post"


@patch("devrel_origin.cli.init.subprocess.run")
@patch("devrel_origin.cli.content._build_kai")
@patch("devrel_origin.cli.content._build_llm_client")
def test_init_chain_runs_first_draft_when_accepted(
    mock_client, mock_build_kai, mock_subproc, tmp_path
):
    """Full chain: auth (existing key) -> doctor -> voice edit -> first draft.
    Kai returns a successful draft; deliverable + trace land in .devrel/."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / ".env").write_text("OPENROUTER_API_KEY=sk-or-test\n")

    mock_client.return_value = MagicMock(generate=AsyncMock(return_value="x"))
    fake_kai = MagicMock()
    fake_kai.execute = AsyncMock(
        return_value={
            "agent": "kai",
            "task": "tutorial on feature flags",
            "status": "generated",
            "content": "# Tutorial\n\nBody.",
            "grounding_sources": ["sdks/python.md"],
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
    try:
        os.chdir(tmp_path)
        # name, url, github_repo, accept voice edit, accept draft, topic, type
        result = runner.invoke(
            app,
            ["init"],
            input="testproj\n\n\ny\ny\ntutorial on feature flags\ntutorial\n",
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "Step 4 of 4" in result.output
    fake_kai.execute.assert_awaited_once_with(
        task="tutorial on feature flags", content_type="tutorial"
    )
    deliverables = list((tmp_path / ".devrel" / "deliverables").glob("*.md"))
    assert len(deliverables) == 1
    assert "Onboarding complete" in result.output
