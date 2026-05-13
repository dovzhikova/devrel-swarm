"""Tests for style.md loading + per-content-type targets parsing."""

from __future__ import annotations

from devrel_origin.project.paths import ProjectPaths
from devrel_origin.quality.style import (
    DEFAULT_TARGETS,
    ContentTypeTargets,
    load_style,
    parse_targets,
)


def _make_paths(tmp_path):
    (tmp_path / ".devrel").mkdir()
    return ProjectPaths.from_root(tmp_path)


def test_load_style_returns_file_text(tmp_path):
    paths = _make_paths(tmp_path)
    paths.style_file.write_text("# Style\n")
    assert load_style(paths) == "# Style\n"


def test_load_style_empty_when_missing(tmp_path):
    paths = _make_paths(tmp_path)
    assert load_style(paths) == ""


def test_parse_targets_extracts_table_rows():
    md = """# Style

Some prose.

## Per-content-type targets

| Content type | Flesch-Kincaid | Mean sentence length | Jargon density |
|---|---|---|---|
| Tutorial | 50-65 | 12-18 words | medium |
| Blog post | 55-70 | 12-20 words | low-medium |
| Cold email | 65-80 | 10-14 words | low |

More prose.
"""
    out = parse_targets(md)
    assert "tutorial" in out
    assert out["tutorial"] == ContentTypeTargets(
        flesch_min=50,
        flesch_max=65,
        sentence_len_min=12,
        sentence_len_max=18,
        jargon_density="medium",
    )
    assert out["blog_post"].flesch_min == 55
    assert out["blog_post"].sentence_len_max == 20
    assert out["cold_email"].jargon_density == "low"


def test_parse_targets_returns_empty_when_no_table():
    assert parse_targets("# Style\n\nNo table here.\n") == {}


def test_parse_targets_skips_malformed_rows():
    md = """| Content type | F-K | Sentence | Jargon |
|---|---|---|---|
| Tutorial | 50-65 | 12-18 words | medium |
| Bad row | not-a-range | nonsense | medium |
| Blog post | 55-70 | 12-20 words | low |
"""
    out = parse_targets(md)
    assert "tutorial" in out
    assert "blog_post" in out
    assert "bad_row" not in out


def test_default_targets_cover_known_content_types():
    expected = {"tutorial", "blog_post", "landing_page", "cold_email", "battle_card"}
    assert expected.issubset(DEFAULT_TARGETS.keys())
    for name, t in DEFAULT_TARGETS.items():
        assert isinstance(t, ContentTypeTargets)
        assert t.flesch_min < t.flesch_max
        assert t.sentence_len_min < t.sentence_len_max
