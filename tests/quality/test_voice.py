"""Tests for voice profile loading."""

from __future__ import annotations

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.quality.voice import load_voice


def _make_paths(tmp_path):
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    return ProjectPaths.from_root(tmp_path)


def test_returns_empty_when_voice_md_missing(tmp_path):
    paths = _make_paths(tmp_path)
    assert load_voice(paths) == ""


def test_returns_full_text_when_voice_md_exists(tmp_path):
    paths = _make_paths(tmp_path)
    body = "# Voice\n\nDirect, technical, mildly irreverent.\n"
    paths.voice_file.write_text(body)
    assert load_voice(paths) == body


def test_strips_no_content(tmp_path):
    """load_voice should return the file verbatim — no normalization."""
    paths = _make_paths(tmp_path)
    body = "  leading whitespace and trailing newline\n  \n"
    paths.voice_file.write_text(body)
    assert load_voice(paths) == body
