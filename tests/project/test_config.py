"""Tests for ProjectConfig loading from TOML."""

from __future__ import annotations

import pytest

from devrel_swarm.project.config import (
    BudgetConfig,
    ConfigError,
    ModelConfig,
    ProjectConfig,
    ProjectIdentity,
)


def _write(tmp_path, body):
    f = tmp_path / "config.toml"
    f.write_text(body)
    return f


def test_load_minimal_config(tmp_path):
    f = _write(tmp_path, '[project]\nname = "openclaw"\n')
    cfg = ProjectConfig.load(f)
    assert cfg.project.name == "openclaw"
    assert cfg.project.url == ""
    assert cfg.project.github_repo is None
    assert cfg.model == ModelConfig()
    assert cfg.budget == BudgetConfig()


def test_load_full_config(tmp_path):
    f = _write(
        tmp_path,
        """
[project]
name = "openclaw"
url = "https://openclaw.ai"
github_repo = "openclaw/openclaw"

[model]
default = "claude-sonnet-4-6"
cheap = "claude-haiku-4-5-20251001"
opus_opt_in = false

[budget]
monthly_usd = 250.0
warn_at_pct = 70
""",
    )
    cfg = ProjectConfig.load(f)
    assert cfg.project == ProjectIdentity(
        name="openclaw",
        url="https://openclaw.ai",
        github_repo="openclaw/openclaw",
    )
    assert cfg.model.default == "claude-sonnet-4-6"
    assert cfg.model.cheap == "claude-haiku-4-5-20251001"
    assert cfg.model.opus_opt_in is False
    assert cfg.budget.monthly_usd == 250.0
    assert cfg.budget.warn_at_pct == 70


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        ProjectConfig.load(tmp_path / "absent.toml")


def test_missing_project_section_raises(tmp_path):
    f = _write(tmp_path, '[model]\ndefault = "x"\n')
    with pytest.raises(ConfigError, match=r"\[project\]"):
        ProjectConfig.load(f)


def test_missing_project_name_raises(tmp_path):
    f = _write(tmp_path, '[project]\nurl = "x"\n')
    with pytest.raises(ConfigError, match="project.name"):
        ProjectConfig.load(f)


def test_partial_model_section_uses_defaults(tmp_path):
    f = _write(
        tmp_path,
        '[project]\nname = "x"\n[model]\ndefault = "claude-opus-4-7"\n',
    )
    cfg = ProjectConfig.load(f)
    assert cfg.model.default == "claude-opus-4-7"
    assert cfg.model.cheap == ModelConfig().cheap  # default preserved
    assert cfg.model.opus_opt_in is True  # default preserved
