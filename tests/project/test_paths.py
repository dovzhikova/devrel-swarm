"""Tests for project root discovery + ProjectPaths."""

from __future__ import annotations

import pytest

from devrel_origin.project.paths import (
    DEVREL_DIR_NAME,
    ProjectNotFoundError,
    ProjectPaths,
    find_devrel_root,
)


def _make_devrel(root):
    """Helper: create a minimal valid .devrel/config.toml under root."""
    d = root / DEVREL_DIR_NAME
    d.mkdir()
    (d / "config.toml").write_text('[project]\nname = "test"\n')
    return root


def test_finds_root_when_cwd_is_project_root(tmp_path):
    project = _make_devrel(tmp_path)
    assert find_devrel_root(project) == project.resolve()


def test_finds_root_from_nested_subdirectory(tmp_path):
    project = _make_devrel(tmp_path)
    nested = project / "src" / "deep" / "module"
    nested.mkdir(parents=True)
    assert find_devrel_root(nested) == project.resolve()


def test_raises_when_no_devrel_anywhere(tmp_path):
    with pytest.raises(ProjectNotFoundError):
        find_devrel_root(tmp_path)


def test_ignores_devrel_dir_without_config_toml(tmp_path):
    """A bare .devrel/ without config.toml shouldn't count as a project."""
    (tmp_path / DEVREL_DIR_NAME).mkdir()
    with pytest.raises(ProjectNotFoundError):
        find_devrel_root(tmp_path)


def test_paths_dataclass_derives_all_paths(tmp_path):
    project = _make_devrel(tmp_path)
    p = ProjectPaths.from_root(project)
    assert p.root == project
    assert p.devrel_dir == project / ".devrel"
    assert p.config_file == project / ".devrel" / "config.toml"
    assert p.voice_file == project / ".devrel" / "voice.md"
    assert p.style_file == project / ".devrel" / "style.md"
    assert p.slop_file == project / ".devrel" / "slop-blocklist.md"
    assert p.kb_dir == project / ".devrel" / "kb"
    assert p.deliverables_dir == project / ".devrel" / "deliverables"
    assert p.context_dir == project / ".devrel" / "context"
    assert p.state_db == project / ".devrel" / "state.db"
    assert p.env_file == project / ".devrel" / ".env"
    assert p.gitignore == project / ".devrel" / ".gitignore"
