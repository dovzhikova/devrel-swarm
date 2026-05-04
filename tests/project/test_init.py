"""Tests for init_project() — idempotent .devrel/ scaffolding."""

from __future__ import annotations

from devrel_swarm.project.init import (
    InitOptions,
    InitResult,
    init_project,
)
from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.project.state import SCHEMA_VERSION, get_schema_version


def test_init_creates_full_scaffold(tmp_path):
    opts = InitOptions(name="openclaw", url="https://openclaw.ai", github_repo="openclaw/openclaw")
    result = init_project(tmp_path, opts)
    p = ProjectPaths.from_root(tmp_path)
    assert p.devrel_dir.is_dir()
    assert p.config_file.is_file()
    assert p.voice_file.is_file()
    assert p.style_file.is_file()
    assert p.slop_file.is_file()
    assert p.kb_dir.is_dir()
    assert p.deliverables_dir.is_dir()
    assert p.context_dir.is_dir()
    assert p.gitignore.is_file()
    assert p.state_db.is_file()
    assert get_schema_version(p.state_db) == SCHEMA_VERSION
    assert isinstance(result, InitResult)
    assert result.created and not result.skipped


def test_init_substitutes_placeholders_in_config(tmp_path):
    opts = InitOptions(name="openclaw", url="https://openclaw.ai", github_repo="openclaw/openclaw")
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    assert 'name = "openclaw"' in body
    assert 'url = "https://openclaw.ai"' in body
    assert 'github_repo = "openclaw/openclaw"' in body
    assert "PROJECT_NAME" not in body
    assert "PROJECT_URL" not in body
    assert "OWNER/REPO" not in body


def test_init_is_idempotent_and_preserves_user_edits(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None)
    init_project(tmp_path, opts)
    voice = tmp_path / ".devrel" / "voice.md"
    voice.write_text("# my custom voice — DO NOT CLOBBER\n")
    result = init_project(tmp_path, opts)
    assert voice.read_text() == "# my custom voice — DO NOT CLOBBER\n"
    assert "voice.md" in result.skipped
    assert "config.toml" in result.skipped


def test_init_handles_missing_github_repo(tmp_path):
    opts = InitOptions(name="solo", url="https://solo.dev", github_repo=None)
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / "config.toml").read_text()
    # github_repo line should be absent or commented out, not literally
    # 'OWNER/REPO' or empty quotes
    assert 'github_repo = "OWNER/REPO"' not in body
    assert 'github_repo = ""' not in body


def test_init_dry_run_creates_nothing(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None, dry_run=True)
    result = init_project(tmp_path, opts)
    assert not (tmp_path / ".devrel").exists()
    assert result.dry_run is True
    assert "config.toml" in result.would_create


def test_init_creates_devrel_gitignore(tmp_path):
    opts = InitOptions(name="x", url="", github_repo=None)
    init_project(tmp_path, opts)
    body = (tmp_path / ".devrel" / ".gitignore").read_text()
    assert "kb/" in body
    assert "deliverables/" in body
    assert "state.db" in body
    assert ".env" in body
