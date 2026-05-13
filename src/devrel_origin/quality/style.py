"""Load style.md and parse the per-content-type targets table.

Content type names are normalized to snake_case for keying (e.g.,
"Blog post" -> "blog_post"). Targets parsing is best-effort: malformed
rows are skipped. If the file or table is missing, callers fall back to
DEFAULT_TARGETS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devrel_origin.project.paths import ProjectPaths


@dataclass(frozen=True)
class ContentTypeTargets:
    flesch_min: int
    flesch_max: int
    sentence_len_min: int
    sentence_len_max: int
    jargon_density: str


DEFAULT_TARGETS: dict[str, ContentTypeTargets] = {
    "tutorial": ContentTypeTargets(50, 65, 12, 18, "medium"),
    "blog_post": ContentTypeTargets(55, 70, 12, 20, "low-medium"),
    "landing_page": ContentTypeTargets(60, 75, 10, 15, "low"),
    "cold_email": ContentTypeTargets(65, 80, 10, 14, "low"),
    "battle_card": ContentTypeTargets(45, 60, 12, 18, "medium-high"),
}


def load_style(paths: ProjectPaths) -> str:
    """Return the full text of `.devrel/style.md`, or "" if missing."""
    if not paths.style_file.is_file():
        return ""
    return paths.style_file.read_text(encoding="utf-8")


_RANGE_RE = re.compile(r"^\s*(\d+)\s*[–-]\s*(\d+)")


def _parse_range(s: str) -> tuple[int, int] | None:
    m = _RANGE_RE.match(s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


def parse_targets(md: str) -> dict[str, ContentTypeTargets]:
    """Parse the per-content-type table in style.md. Looks for the first
    pipe-table whose header row contains 'Flesch' (case-insensitive) and
    'Jargon'. Returns a snake_case-keyed dict of ContentTypeTargets.
    """
    lines = md.splitlines()
    out: dict[str, ContentTypeTargets] = {}
    in_table = False
    header_seen = False
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            if in_table:
                break
            continue
        if not header_seen:
            lower = line.lower()
            if "jargon" in lower and ("flesch" in lower or "f-k" in lower or "sentence" in lower):
                header_seen = True
                in_table = True
            continue
        # Skip the markdown separator row (|---|---|...).
        if set(line.replace("|", "").strip()) <= set("- "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name_cell, flesch_cell, sentence_cell, jargon_cell = cells[:4]
        flesch = _parse_range(flesch_cell)
        sentence = _parse_range(sentence_cell)
        if flesch is None or sentence is None:
            continue
        name = _normalize_name(name_cell)
        if not name:
            continue
        out[name] = ContentTypeTargets(
            flesch_min=flesch[0],
            flesch_max=flesch[1],
            sentence_len_min=sentence[0],
            sentence_len_max=sentence[1],
            jargon_density=jargon_cell,
        )
    return out


def get_targets(content_type: str, md: str) -> ContentTypeTargets:
    """Resolve targets for a content type: prefer parsed style.md table,
    then fall back to DEFAULT_TARGETS. Raises KeyError if neither source
    has the type."""
    parsed = parse_targets(md)
    if content_type in parsed:
        return parsed[content_type]
    if content_type in DEFAULT_TARGETS:
        return DEFAULT_TARGETS[content_type]
    raise KeyError(f"Unknown content_type: {content_type!r}")
