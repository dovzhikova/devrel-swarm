# devrel-swarm CLI — Phase 3: Quality Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 8-stage editorial quality pipeline that turns AI-written drafts into senior-editor-quality content, integrate it into the three content-producing agents (Kai, Mox, Pax), and expose it via two new CLI verbs (`devrel content draft` and `devrel content audit`). Every piece of content the system produces flows through this pipeline.

**Architecture:** Six new modules under `src/devrel_swarm/quality/` (voice, style, slop, persona, readability, editorial). The `editorial.run_pipeline` function orchestrates the 8 stages and is the single entry point the agents call. Stages 2–4 (developmental → line → copy edit) run as discrete `generate_with_revision` loops with `min_score=7, max_rounds=2`. Stage 5 (anti-slop) self-corrects once. Stages 6–7 (persona, readability) are scoring-only on the stage-5 output; if either fails, control returns to stage 4 once. Stage 8 reuses the existing Sentinel brand audit unchanged. Cheap stages (5–7) run on Haiku; editorial stages (2–4) run on Sonnet. Cost ≈ 2.5–4× a single revision loop, with prompt caching pulling it toward the lower bound.

**Tech Stack:** Python 3.12+, the existing `LLMClient` (which already supports `model="haiku"` overrides via `MODELS` dict), `re` stdlib for slop matching, pure-Python Flesch Reading Ease + sentence statistics for readability, pytest + respx for prompt-mocked tests.

**Spec:** `docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md`
**Phase 1 + 2 (prerequisites, both merged):** `be971bd` and `121187e` on `main`.

---

## Spec correction (locked here)

The spec lists "Kai, Mox, Pax, and Vox's script generator" as the four content-producing agents. Inspection of `src/devrel_swarm/core/vox.py` and `src/devrel_swarm/core/video/script_parser.py` shows neither uses an LLM — Vox is a deterministic markdown-to-video parser that consumes Kai's output and runs Playwright/FFmpeg/TTS. There is no script-generator LLM call to integrate with. **Phase 3 integrates the quality pipeline into Kai, Mox, and Pax only — three agents, not four.** If a future phase adds an LLM-driven script generator inside Vox, that call site should also use `quality.editorial.run_pipeline` for parity.

## File structure after Phase 3

```
src/devrel_swarm/
  quality/                          NEW
    __init__.py                     # public exports: run_pipeline, EditorialResult
    voice.py                        # load_voice(paths) -> str
    style.py                        # load_style(paths) -> str ; parse_targets(md) -> dict
    slop.py                         # parse_blocklist, find_slop, llm_lint, force_rewrite
    persona.py                      # test_against_persona -> PersonaResult
    readability.py                  # compute_readability, check_against_target
    editorial.py                    # run_pipeline (8-stage orchestrator) + StageResult, EditorialResult
  cli/
    content.py                      NEW   `devrel content draft|audit`
  core/
    kai.py                          MODIFY   replace generate_with_revision call with run_pipeline
    mox.py                          MODIFY   same surgical change
    pax.py                          MODIFY   same surgical change
tests/
  quality/                          NEW
    __init__.py
    test_voice.py
    test_style.py
    test_slop.py
    test_persona.py
    test_readability.py
    test_editorial.py
  cli/
    test_content_command.py         NEW
  test_kai.py                       MODIFY  update mock expectations to match new call site
  test_mox.py                       MODIFY  same
  test_pax.py                       MODIFY  same
```

No other code moves or restructures. Phase 1's src-layout, Phase 2's `project/` and `cli/` packages stay put.

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree off `main`**

Use **superpowers:using-git-worktrees** to create a worktree at `.worktrees/cli-phase3-quality` on a new branch `feat/cli-phase3-quality`. Confirm `main` is at `121187e` (Phase 2 merge) or later before branching.

- [ ] **Step 2: Confirm starting state**

```bash
git rev-parse --abbrev-ref HEAD
git log --oneline -1
test -d src/devrel_swarm/project && test -d src/devrel_swarm/cli && echo "Phase 2 layout present"
```
Expected: branch `feat/cli-phase3-quality`, HEAD at `121187e` (or later), `Phase 2 layout present` printed.

- [ ] **Step 3: Activate venv + reinstall + capture baseline**

```bash
/opt/homebrew/bin/python3.13 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.preflight.log 2>&1 && echo "exit=$?"
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort > /tmp/pytest.failures.phase3.before.txt
wc -l /tmp/pytest.failures.phase3.before.txt
```
Expected: `exit=0`, `598 passed, 22 failed`, `22` lines.

---

## Task 1: `quality/__init__.py` skeleton

**Files:**
- Create: `src/devrel_swarm/quality/__init__.py`
- Create: `tests/quality/__init__.py`

- [ ] **Step 1: Create the empty package init files**

Write `src/devrel_swarm/quality/__init__.py`:
```python
"""8-stage editorial quality pipeline for content-producing agents.

Public entry point is `run_pipeline` in `editorial.py`. Agents (Kai, Mox,
Pax) replace their single `generate_with_revision` call with one call to
`run_pipeline`. Output includes the final text plus a revision trace
spanning every stage.
"""
```

Write `tests/quality/__init__.py` (empty):
```python
```

- [ ] **Step 2: Commit**

```bash
git add src/devrel_swarm/quality/__init__.py tests/quality/__init__.py
git commit -m "feat(quality): add quality/ package skeleton"
```

---

## Task 2: `quality/voice.py` — load voice.md

**Files:**
- Create: `src/devrel_swarm/quality/voice.py`
- Create: `tests/quality/test_voice.py`

- [ ] **Step 1: Write failing test**

Write `tests/quality/test_voice.py`:
```python
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
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_voice.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError on `devrel_swarm.quality.voice`.

- [ ] **Step 3: Implement `voice.py`**

Write `src/devrel_swarm/quality/voice.py`:
```python
"""Load voice.md as a single string for prompt injection."""

from __future__ import annotations

from devrel_swarm.project.paths import ProjectPaths


def load_voice(paths: ProjectPaths) -> str:
    """Return the full text of `.devrel/voice.md`, or "" if the file is
    missing. The orchestrator injects this verbatim into editorial-stage
    system prompts as the project's voice contract.
    """
    if not paths.voice_file.is_file():
        return ""
    return paths.voice_file.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_voice.py -v --no-cov 2>&1 | tail -5
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/voice.py tests/quality/test_voice.py
git commit -m "feat(quality): add voice profile loader"
```

---

## Task 3: `quality/style.py` — load + parse targets table

**Files:**
- Create: `src/devrel_swarm/quality/style.py`
- Create: `tests/quality/test_style.py`

- [ ] **Step 1: Write failing test**

Write `tests/quality/test_style.py`:
```python
"""Tests for style.md loading + per-content-type targets parsing."""

from __future__ import annotations

import pytest

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.quality.style import (
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
        flesch_min=50, flesch_max=65,
        sentence_len_min=12, sentence_len_max=18,
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
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_style.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError.

- [ ] **Step 3: Implement `style.py`**

Write `src/devrel_swarm/quality/style.py`:
```python
"""Load style.md and parse the per-content-type targets table.

Content type names are normalized to snake_case for keying (e.g.,
"Blog post" -> "blog_post"). Targets parsing is best-effort: malformed
rows are skipped. If the file or table is missing, callers fall back to
DEFAULT_TARGETS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devrel_swarm.project.paths import ProjectPaths


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
            if "flesch" in line.lower() and "jargon" in line.lower():
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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_style.py -v --no-cov 2>&1 | tail -10
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/style.py tests/quality/test_style.py
git commit -m "feat(quality): add style loader + targets table parser"
```

---

## Task 4: `quality/slop.py` — blocklist matching + LLM lint + force-rewrite

**Files:**
- Create: `src/devrel_swarm/quality/slop.py`
- Create: `tests/quality/test_slop.py`

- [ ] **Step 1: Write failing test**

Write `tests/quality/test_slop.py`:
```python
"""Tests for slop blocklist matching, LLM lint, and force-rewrite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.quality.slop import (
    SlopHit,
    find_slop,
    force_rewrite,
    llm_lint,
    parse_blocklist,
)


def test_parse_blocklist_strips_comments_and_blanks():
    md = """# Anti-slop blocklist

## Hedge words
delve
furthermore

## CTAs
learn more
get started today
"""
    out = parse_blocklist(md)
    assert out == ["delve", "furthermore", "learn more", "get started today"]


def test_parse_blocklist_lowercases():
    out = parse_blocklist("Delve\nFURTHERMORE\n")
    assert out == ["delve", "furthermore"]


def test_find_slop_word_boundary_match():
    text = "We delve into the topic, furthermore the tapestry unfolds."
    hits = find_slop(text, ["delve", "furthermore", "tapestry"])
    assert {h.phrase for h in hits} == {"delve", "furthermore", "tapestry"}


def test_find_slop_case_insensitive():
    text = "DELVE into this. Furthermore."
    hits = find_slop(text, ["delve", "furthermore"])
    assert len(hits) == 2


def test_find_slop_does_not_match_substrings():
    """`delve` should not match `delivery` or `develop`."""
    text = "We develop and delivery great things."
    hits = find_slop(text, ["delve"])
    assert hits == []


def test_find_slop_handles_multi_word_phrases():
    text = "Get started today with our platform."
    hits = find_slop(text, ["get started today"])
    assert len(hits) == 1
    assert hits[0].phrase == "get started today"


def test_find_slop_empty_when_no_matches():
    assert find_slop("Direct, sharp, no fluff.", ["delve", "tapestry"]) == []


@pytest.mark.asyncio
async def test_llm_lint_calls_haiku_and_parses_phrases():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("phrase one\nphrase two\n", None))
    out = await llm_lint("some draft text", "voice prose", client)
    assert out == ["phrase one", "phrase two"]
    # Verify it called with model="haiku" for cost.
    call_kwargs = client.generate.await_args.kwargs
    assert call_kwargs.get("model") == "haiku"


@pytest.mark.asyncio
async def test_llm_lint_returns_empty_on_empty_response():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("", None))
    assert await llm_lint("draft", "voice", client) == []


@pytest.mark.asyncio
async def test_llm_lint_filters_blank_lines_and_bullets():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value=("- phrase one\n  \n* phrase two\n#commented\n", None)
    )
    out = await llm_lint("draft", "voice", client)
    assert out == ["phrase one", "phrase two"]


@pytest.mark.asyncio
async def test_force_rewrite_passes_hits_to_llm_and_returns_text():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("the rewritten text", None))
    hits = [SlopHit(phrase="delve", start=0, end=5)]
    out = await force_rewrite("delve into x", hits, ["extra-slop"], "voice", client)
    assert out == "the rewritten text"
    user_prompt = client.generate.await_args.kwargs["user_prompt"]
    # Must list every flagged item in the rewrite prompt.
    assert "delve" in user_prompt
    assert "extra-slop" in user_prompt
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_slop.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError.

- [ ] **Step 3: Implement `slop.py`**

Write `src/devrel_swarm/quality/slop.py`:
```python
"""Anti-slop pipeline stage 5.

Three-step matching:
1. Regex blocklist (deterministic, fast). Word-boundary, case-insensitive.
2. LLM lint (Haiku). Catches context-sensitive slop the regex misses
   (verbose intros, vague intensifiers in unusual phrasings).
3. Force-rewrite (Sonnet). One targeted rewrite call with all hits listed.
   If the rewrite still trips the blocklist on re-check, the orchestrator
   aborts loud — see editorial.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SlopHit:
    phrase: str
    start: int
    end: int


def parse_blocklist(md: str) -> list[str]:
    """Parse `slop-blocklist.md`. Returns lowercased phrases, one per
    non-comment, non-blank line. Lines starting with `#` are comments."""
    out: list[str] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.lower())
    return out


def find_slop(text: str, blocklist: list[str]) -> list[SlopHit]:
    """Word-boundary, case-insensitive regex match. Returns one hit per
    occurrence, in order."""
    hits: list[SlopHit] = []
    text_lower = text.lower()
    for phrase in blocklist:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        for m in re.finditer(pattern, text_lower):
            hits.append(SlopHit(phrase=phrase, start=m.start(), end=m.end()))
    hits.sort(key=lambda h: h.start)
    return hits


_LINT_SYSTEM = (
    "You are an editor screening AI-written content for tells the regex "
    "blocklist would miss: verbose intros, vague intensifiers in unusual "
    "phrasings, hedging that doesn't appear in the blocklist verbatim. "
    "Return a flat list, one phrase per line, lowercase, no bullets, no "
    "explanations. If nothing concerning, return an empty response."
)


def _normalize_lint_lines(raw: str) -> list[str]:
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip leading bullets / ordinals.
        s = re.sub(r"^[\-\*•\d\.\)]+\s*", "", s)
        if s:
            out.append(s.lower())
    return out


async def llm_lint(text: str, voice: str, llm_client) -> list[str]:
    """Haiku-powered second-pass slop detector."""
    user = (
        "Voice contract for this product:\n\n" + (voice or "(none)") + "\n\n"
        "Content to screen:\n\n" + text + "\n\n"
        "List the phrases that read as AI-written, one per line. Empty if clean."
    )
    raw, _trace = await llm_client.generate(
        system_prompt=_LINT_SYSTEM,
        user_prompt=user,
        model="haiku",
    )
    return _normalize_lint_lines(raw)


_REWRITE_SYSTEM = (
    "You are a rewrite editor. The reader has flagged specific phrases as "
    "AI-written. Rewrite the content so none of the flagged phrases (or "
    "their close synonyms) appear, while preserving meaning, structure, "
    "and the project's voice. Return only the rewritten content — no "
    "preamble, no explanation."
)


async def force_rewrite(
    text: str,
    regex_hits: list[SlopHit],
    llm_lint_hits: list[str],
    voice: str,
    llm_client,
) -> str:
    """Single Sonnet rewrite with the full flagged list. Caller is
    responsible for re-running `find_slop` + `llm_lint` to verify the
    rewrite cleared the issues."""
    flagged = sorted({h.phrase for h in regex_hits} | set(llm_lint_hits))
    flagged_listing = "\n".join(f"- {p}" for p in flagged)
    user = (
        "Voice contract:\n\n" + (voice or "(none)") + "\n\n"
        "Flagged phrases (do not let any of these appear in the rewrite, "
        "and avoid close synonyms):\n\n" + flagged_listing + "\n\n"
        "Original content:\n\n" + text
    )
    rewritten, _trace = await llm_client.generate(
        system_prompt=_REWRITE_SYSTEM,
        user_prompt=user,
        model="sonnet",
    )
    return rewritten.strip()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_slop.py -v --no-cov 2>&1 | tail -15
```
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/slop.py tests/quality/test_slop.py
git commit -m "feat(quality): add anti-slop matcher + LLM lint + force-rewrite"
```

---

## Task 5: `quality/readability.py` — Flesch + sentence variance + jargon

**Files:**
- Create: `src/devrel_swarm/quality/readability.py`
- Create: `tests/quality/test_readability.py`

- [ ] **Step 1: Write failing test**

Write `tests/quality/test_readability.py`:
```python
"""Tests for pure-Python readability scoring."""

from __future__ import annotations

import pytest

from devrel_swarm.quality.readability import (
    ReadabilityScores,
    check_against_target,
    compute_readability,
    count_syllables,
)
from devrel_swarm.quality.style import ContentTypeTargets


def test_count_syllables_basic():
    assert count_syllables("the") == 1
    assert count_syllables("simple") == 2
    assert count_syllables("syllable") == 3
    assert count_syllables("readability") == 5
    assert count_syllables("") == 0


def test_count_syllables_silent_e_handling():
    assert count_syllables("rate") == 1
    assert count_syllables("create") == 2


def test_compute_readability_basic_text():
    text = (
        "The cat sat on the mat. "
        "It was a small cat. "
        "The mat was red and warm."
    )
    s = compute_readability(text)
    assert isinstance(s, ReadabilityScores)
    assert s.word_count == 17
    assert s.sentence_count == 3
    assert 4 < s.mean_sentence_length < 7
    assert 70 < s.flesch_reading_ease < 110  # very easy text


def test_compute_readability_empty_returns_zeros():
    s = compute_readability("")
    assert s.word_count == 0
    assert s.sentence_count == 0
    assert s.flesch_reading_ease == 0.0


def test_compute_readability_handles_single_sentence():
    s = compute_readability("Hello world.")
    assert s.sentence_count == 1
    assert s.word_count == 2


def test_jargon_density_long_words():
    text = "The implementation orchestration synchronization was straightforward."
    s = compute_readability(text)
    # 4 of 5 content words are >12 chars.
    assert s.jargon_density > 0.5


def test_check_against_target_pass():
    target = ContentTypeTargets(50, 75, 10, 20, "medium")
    scores = ReadabilityScores(
        flesch_reading_ease=60.0,
        mean_sentence_length=15.0,
        sentence_length_variance=4.0,
        jargon_density=0.1,
        word_count=100,
        sentence_count=8,
    )
    assert check_against_target(scores, target) == []


def test_check_against_target_flags_flesch_below_min():
    target = ContentTypeTargets(50, 75, 10, 20, "medium")
    scores = ReadabilityScores(
        flesch_reading_ease=30.0,  # below 50
        mean_sentence_length=15.0,
        sentence_length_variance=4.0,
        jargon_density=0.1,
        word_count=100,
        sentence_count=8,
    )
    issues = check_against_target(scores, target)
    assert any("Flesch" in i and "below" in i for i in issues)


def test_check_against_target_flags_sentence_length_above_max():
    target = ContentTypeTargets(50, 75, 10, 20, "medium")
    scores = ReadabilityScores(
        flesch_reading_ease=60.0,
        mean_sentence_length=30.0,  # above 20
        sentence_length_variance=4.0,
        jargon_density=0.1,
        word_count=100,
        sentence_count=8,
    )
    issues = check_against_target(scores, target)
    assert any("sentence" in i.lower() for i in issues)


def test_check_against_target_drift_tolerance_is_ten_points():
    """Plan: 'flags drift > ±10 points from the Flesch target.'"""
    target = ContentTypeTargets(50, 65, 10, 20, "medium")
    # 41 is 9 below 50 — within tolerance, no flag
    in_range = ReadabilityScores(41.0, 15.0, 4.0, 0.1, 100, 8)
    assert check_against_target(in_range, target) == []
    # 39 is 11 below 50 — outside tolerance, flag it
    out = ReadabilityScores(39.0, 15.0, 4.0, 0.1, 100, 8)
    assert check_against_target(out, target) != []
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_readability.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError.

- [ ] **Step 3: Implement `readability.py`**

Write `src/devrel_swarm/quality/readability.py`:
```python
"""Pure-Python readability scoring: Flesch Reading Ease, sentence-length
statistics, jargon density. No LLM calls. Used as stage 7 of the
editorial pipeline.

Flesch Reading Ease formula:
  FRE = 206.835 - 1.015 * (words/sentences) - 84.6 * (syllables/words)

Higher FRE = easier to read. Style.md targets per content-type are
expressed in this scale (e.g., tutorial 50-65 = "fairly difficult").

Jargon density: fraction of content words >= 12 characters. Imperfect but
catches the obvious "academic-ese" drift; tuned alongside style.md targets.

Drift tolerance: a score that's within 10 points of the target range
(below min or above max) does not flag. The pipeline only reverts to copy
edit (stage 4) if drift exceeds 10 points OR sentence length exits the
target range entirely.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from devrel_swarm.quality.style import ContentTypeTargets

DRIFT_TOLERANCE = 10  # Flesch points

_VOWELS = set("aeiouy")
_WORD_RE = re.compile(r"\b[a-zA-Z']+\b")
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def count_syllables(word: str) -> int:
    """Approximate syllable count: count vowel groups, drop a single
    silent terminal 'e' if more than one syllable remains."""
    if not word:
        return 0
    word = word.lower()
    syllables = 0
    prev_vowel = False
    for ch in word:
        is_v = ch in _VOWELS
        if is_v and not prev_vowel:
            syllables += 1
        prev_vowel = is_v
    if word.endswith("e") and syllables > 1:
        syllables -= 1
    return max(1, syllables)


@dataclass(frozen=True)
class ReadabilityScores:
    flesch_reading_ease: float
    mean_sentence_length: float
    sentence_length_variance: float
    jargon_density: float
    word_count: int
    sentence_count: int


def _split_sentences(text: str) -> list[str]:
    """Split on terminal punctuation. Filters empty fragments."""
    pieces = _SENTENCE_END.split(text)
    out = [p.strip() for p in pieces if p.strip()]
    return out


def _words_in(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def compute_readability(text: str) -> ReadabilityScores:
    """Compute all readability metrics for `text`."""
    sentences = _split_sentences(text)
    sentence_count = len(sentences)
    words = _words_in(text)
    word_count = len(words)

    if word_count == 0 or sentence_count == 0:
        return ReadabilityScores(0.0, 0.0, 0.0, 0.0, word_count, sentence_count)

    syllable_total = sum(count_syllables(w) for w in words)
    flesch = (
        206.835
        - 1.015 * (word_count / sentence_count)
        - 84.6 * (syllable_total / word_count)
    )

    sentence_lengths = [len(_words_in(s)) for s in sentences]
    mean_sl = statistics.mean(sentence_lengths)
    var_sl = statistics.pvariance(sentence_lengths) if sentence_count > 1 else 0.0

    long_words = sum(1 for w in words if len(w) >= 12)
    jargon = long_words / word_count

    return ReadabilityScores(
        flesch_reading_ease=round(flesch, 2),
        mean_sentence_length=round(mean_sl, 2),
        sentence_length_variance=round(var_sl, 2),
        jargon_density=round(jargon, 4),
        word_count=word_count,
        sentence_count=sentence_count,
    )


def check_against_target(
    scores: ReadabilityScores,
    target: ContentTypeTargets,
) -> list[str]:
    """Return a list of human-readable issues, or empty if scores meet the
    target within DRIFT_TOLERANCE for Flesch and exactly for sentence length."""
    issues: list[str] = []

    if scores.flesch_reading_ease < target.flesch_min - DRIFT_TOLERANCE:
        issues.append(
            f"Flesch reading ease {scores.flesch_reading_ease} is "
            f"{target.flesch_min - scores.flesch_reading_ease:.1f} points below "
            f"min {target.flesch_min} (drift > {DRIFT_TOLERANCE})."
        )
    elif scores.flesch_reading_ease > target.flesch_max + DRIFT_TOLERANCE:
        issues.append(
            f"Flesch reading ease {scores.flesch_reading_ease} is "
            f"{scores.flesch_reading_ease - target.flesch_max:.1f} points above "
            f"max {target.flesch_max} (drift > {DRIFT_TOLERANCE})."
        )

    if scores.mean_sentence_length < target.sentence_len_min:
        issues.append(
            f"Mean sentence length {scores.mean_sentence_length} below "
            f"min {target.sentence_len_min}."
        )
    elif scores.mean_sentence_length > target.sentence_len_max:
        issues.append(
            f"Mean sentence length {scores.mean_sentence_length} above "
            f"max {target.sentence_len_max}."
        )

    return issues
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_readability.py -v --no-cov 2>&1 | tail -15
```
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/readability.py tests/quality/test_readability.py
git commit -m "feat(quality): add Flesch + sentence-stats + jargon-density scoring"
```

---

## Task 6: `quality/persona.py` — skeptical-dev reader test

**Files:**
- Create: `src/devrel_swarm/quality/persona.py`
- Create: `tests/quality/test_persona.py`

- [ ] **Step 1: Write failing test**

Write `tests/quality/test_persona.py`:
```python
"""Tests for the skeptical-dev persona reader test."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.quality.persona import (
    PersonaResult,
    test_against_persona,
)


@pytest.mark.asyncio
async def test_returns_score_and_weak_sections_from_haiku():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value=(
            '{"score": 7, "weak_sections": ["The intro hedges too much."], '
            '"feedback": "Solid, but the conclusion is weak."}',
            None,
        )
    )
    out = await test_against_persona(
        text="some draft", content_type="tutorial", voice="direct", llm_client=client
    )
    assert isinstance(out, PersonaResult)
    assert out.score == 7
    assert out.weak_sections == ["The intro hedges too much."]
    assert "weak" in out.feedback.lower()


@pytest.mark.asyncio
async def test_uses_haiku_model():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value=('{"score": 8, "weak_sections": [], "feedback": "ok"}', None)
    )
    await test_against_persona(text="x", content_type="blog_post", voice="", llm_client=client)
    assert client.generate.await_args.kwargs["model"] == "haiku"


@pytest.mark.asyncio
async def test_clamps_score_to_1_10():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value=('{"score": 99, "weak_sections": [], "feedback": "x"}', None)
    )
    out = await test_against_persona(text="x", content_type="tutorial", voice="", llm_client=client)
    assert 1 <= out.score <= 10


@pytest.mark.asyncio
async def test_falls_back_when_response_not_json():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("not json", None))
    out = await test_against_persona(text="x", content_type="tutorial", voice="", llm_client=client)
    assert out.score == 5  # neutral fallback
    assert "could not parse" in out.feedback.lower()


@pytest.mark.asyncio
async def test_includes_content_type_in_prompt():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value=('{"score": 7, "weak_sections": [], "feedback": "ok"}', None)
    )
    await test_against_persona(
        text="draft", content_type="cold_email", voice="brief", llm_client=client
    )
    user = client.generate.await_args.kwargs["user_prompt"]
    assert "cold_email" in user or "cold email" in user.lower()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_persona.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError.

- [ ] **Step 3: Implement `persona.py`**

Write `src/devrel_swarm/quality/persona.py`:
```python
"""Stage 6 of the editorial pipeline.

Single Haiku call against a fixed persona — "skeptical senior backend
developer" — that scores the draft 1-10 on resonance and flags weak
sections with quoted excerpts. The orchestrator uses the score as a soft
gate: if it falls below 7, control returns to copy-edit (stage 4) once
with the persona feedback attached as a critique.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaResult:
    score: int
    weak_sections: list[str]
    feedback: str


_SYSTEM_PROMPT = """You are a skeptical senior backend developer with 10+ years of experience. You're allergic to marketing fluff, consultant-speak, and AI-style hedging. You've read enough developer-targeted content to instantly spot when a piece is generic, surface-level, or written for a project the author hasn't actually used.

Score the content on a 1-10 scale:
- 10 = This made me want to try the product immediately. Specific, concrete, technical.
- 7-9 = Solid. I'd send it to a teammate.
- 4-6 = Generic. Could be about any product. Skim-level.
- 1-3 = Pure marketing. I'd close the tab.

Identify up to 3 weak sections — quote them verbatim or paraphrase tightly. Be honest; don't pad.

Return strict JSON:
{
  "score": 1-10,
  "weak_sections": ["…", "…"],
  "feedback": "1-2 sentences on what's working and what isn't"
}
"""


def _coerce_result(raw: str) -> PersonaResult:
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return PersonaResult(
            score=5,
            weak_sections=[],
            feedback="Could not parse persona response as JSON.",
        )
    score = int(data.get("score", 5))
    score = max(1, min(10, score))
    weak = list(data.get("weak_sections", []) or [])
    feedback = str(data.get("feedback", ""))
    return PersonaResult(score=score, weak_sections=weak, feedback=feedback)


async def test_against_persona(
    *,
    text: str,
    content_type: str,
    voice: str,
    llm_client,
) -> PersonaResult:
    """Single Haiku call. Returns a structured score + weak-sections + feedback."""
    user = (
        f"Content type: {content_type}\n\n"
        "Voice contract for this product:\n\n"
        + (voice or "(no voice profile yet)")
        + "\n\nContent to evaluate:\n\n"
        + text
    )
    raw, _trace = await llm_client.generate(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user,
        model="haiku",
    )
    return _coerce_result(raw)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_persona.py -v --no-cov 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/persona.py tests/quality/test_persona.py
git commit -m "feat(quality): add skeptical-dev persona scorer"
```

---

## Task 7: `quality/editorial.py` — 8-stage orchestrator

**Files:**
- Create: `src/devrel_swarm/quality/editorial.py`
- Create: `tests/quality/test_editorial.py`

This is the largest module in Phase 3. It composes every primitive built so far into a single async pipeline.

- [ ] **Step 1: Write failing tests**

Write `tests/quality/test_editorial.py`:
```python
"""Tests for the 8-stage editorial pipeline orchestrator."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.quality.editorial import (
    AbortLoud,
    EditorialResult,
    StageResult,
    run_pipeline,
)


def _project(tmp_path) -> ProjectPaths:
    """Build a .devrel/ with voice/style/slop files for the pipeline to read."""
    d = tmp_path / ".devrel"
    d.mkdir()
    (d / "voice.md").write_text("# Voice\n\nDirect, technical.\n")
    (d / "style.md").write_text("# Style\n\nSentence case headings.\n")
    (d / "slop-blocklist.md").write_text("delve\nfurthermore\nin conclusion\n")
    return ProjectPaths.from_root(tmp_path)


def _mock_client_for_clean_run():
    """A mock LLMClient that returns clean text at every stage."""
    client = MagicMock()
    # Editorial stages: generate_with_revision returns (text, trace) tuple.
    client.generate_with_revision = AsyncMock(
        return_value=(
            "Clean revised text without any flagged phrases.",
            MagicMock(final_score=8, revision_rounds=0, critiques=[]),
        )
    )
    # Slop LLM lint: empty (no LLM-detected slop).
    # Persona: high score, no weak sections.
    # Force-rewrite: not called on a clean run.
    async def _generate(*, system_prompt, user_prompt, model, **kwargs):
        if "screening AI-written content" in system_prompt:  # llm_lint
            return ("", None)
        if "skeptical senior backend developer" in system_prompt:  # persona
            return ('{"score": 8, "weak_sections": [], "feedback": "solid"}', None)
        if "rewrite editor" in system_prompt:  # force_rewrite (shouldn't fire)
            return ("rewritten", None)
        return ("", None)
    client.generate = AsyncMock(side_effect=_generate)
    client.set_agent = MagicMock()
    return client


@pytest.mark.asyncio
async def test_clean_run_produces_8_stage_result(tmp_path):
    paths = _project(tmp_path)
    client = _mock_client_for_clean_run()

    result = await run_pipeline(
        initial_draft="A clear sharp opening sentence about the product.",
        content_type="tutorial",
        project_paths=paths,
        llm_client=client,
    )

    assert isinstance(result, EditorialResult)
    assert result.flagged is False
    # 5 stages produce StageResults: developmental, line, copy, slop, persona, readability, audit
    # (8 stages in spec; stage 1 is generate, which is the input here, so we record 7 stages)
    stage_names = [s.name for s in result.stages]
    assert "developmental_edit" in stage_names
    assert "line_edit" in stage_names
    assert "copy_edit" in stage_names
    assert "anti_slop" in stage_names
    assert "persona" in stage_names
    assert "readability" in stage_names
    # Brand audit is run by Sentinel — represented as 'brand_audit' if invoked, else absent.
    # See test below for opt-in audit case.


@pytest.mark.asyncio
async def test_editorial_stages_call_generate_with_revision_with_min_score_7(tmp_path):
    paths = _project(tmp_path)
    client = _mock_client_for_clean_run()
    await run_pipeline(
        initial_draft="x", content_type="tutorial", project_paths=paths, llm_client=client
    )
    # All three editorial passes should use min_score=7, max_rounds=2
    for call in client.generate_with_revision.await_args_list:
        kwargs = call.kwargs
        assert kwargs.get("min_score") == 7
        assert kwargs.get("max_rounds") == 2


@pytest.mark.asyncio
async def test_slop_hit_triggers_force_rewrite(tmp_path):
    paths = _project(tmp_path)
    # Editorial returns text with slop. Force-rewrite returns clean text.
    client = MagicMock()
    client.set_agent = MagicMock()
    client.generate_with_revision = AsyncMock(
        return_value=("This delves into the topic. Furthermore, look at this.",
                      MagicMock(final_score=8, revision_rounds=0, critiques=[]))
    )
    rewrite_text = "This explores the topic. Look at this."
    async def _generate(*, system_prompt, user_prompt, model, **kwargs):
        if "screening AI-written content" in system_prompt:
            return ("", None)
        if "skeptical senior backend developer" in system_prompt:
            return ('{"score": 8, "weak_sections": [], "feedback": "ok"}', None)
        if "rewrite editor" in system_prompt:
            return (rewrite_text, None)
        return ("", None)
    client.generate = AsyncMock(side_effect=_generate)

    result = await run_pipeline(
        initial_draft="x", content_type="tutorial", project_paths=paths, llm_client=client
    )
    # The final text must be the post-rewrite text, no slop.
    assert "delve" not in result.final_text.lower()
    slop_stage = next(s for s in result.stages if s.name == "anti_slop")
    assert "rewrite_applied" in (slop_stage.detail or "")


@pytest.mark.asyncio
async def test_slop_persists_after_rewrite_aborts_loud(tmp_path):
    paths = _project(tmp_path)
    # Editorial returns text with slop. Rewrite ALSO contains slop.
    client = MagicMock()
    client.set_agent = MagicMock()
    client.generate_with_revision = AsyncMock(
        return_value=("delves and furthermore.",
                      MagicMock(final_score=8, revision_rounds=0, critiques=[]))
    )
    async def _generate(*, system_prompt, user_prompt, model, **kwargs):
        if "screening AI-written content" in system_prompt:
            return ("", None)
        if "skeptical senior backend developer" in system_prompt:
            return ('{"score": 8, "weak_sections": [], "feedback": "ok"}', None)
        if "rewrite editor" in system_prompt:
            return ("delves still here.", None)  # rewrite still has slop
        return ("", None)
    client.generate = AsyncMock(side_effect=_generate)

    with pytest.raises(AbortLoud) as exc_info:
        await run_pipeline(
            initial_draft="x", content_type="tutorial",
            project_paths=paths, llm_client=client,
        )
    assert "delve" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_low_persona_score_returns_to_copy_edit_once(tmp_path):
    paths = _project(tmp_path)
    client = MagicMock()
    client.set_agent = MagicMock()
    # Stages 2 (developmental), 3 (line), 4 (copy) — first pass.
    # Then stage 4 fires AGAIN after persona fails. So 4 calls total expected.
    client.generate_with_revision = AsyncMock(
        side_effect=[
            ("v1 dev", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v1 line", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v1 copy", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v2 copy", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
        ]
    )
    persona_calls = iter([
        '{"score": 4, "weak_sections": ["bad intro"], "feedback": "weak"}',
        '{"score": 8, "weak_sections": [], "feedback": "fixed"}',
    ])
    async def _generate(*, system_prompt, user_prompt, model, **kwargs):
        if "screening AI-written content" in system_prompt:
            return ("", None)
        if "skeptical senior backend developer" in system_prompt:
            return (next(persona_calls), None)
        return ("", None)
    client.generate = AsyncMock(side_effect=_generate)

    result = await run_pipeline(
        initial_draft="x", content_type="tutorial", project_paths=paths, llm_client=client
    )
    # First persona pass fails; copy edit re-runs once; second persona passes.
    assert client.generate_with_revision.await_count == 4
    persona_stages = [s for s in result.stages if s.name == "persona"]
    assert len(persona_stages) == 2  # both attempts logged
    assert result.flagged is False  # second persona passed


@pytest.mark.asyncio
async def test_persona_fails_twice_logs_and_ships_flagged(tmp_path):
    paths = _project(tmp_path)
    client = MagicMock()
    client.set_agent = MagicMock()
    client.generate_with_revision = AsyncMock(
        side_effect=[
            ("v1 dev", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v1 line", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v1 copy", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
            ("v2 copy", MagicMock(final_score=8, revision_rounds=0, critiques=[])),
        ]
    )
    async def _generate(*, system_prompt, user_prompt, model, **kwargs):
        if "screening AI-written content" in system_prompt:
            return ("", None)
        if "skeptical senior backend developer" in system_prompt:
            return ('{"score": 4, "weak_sections": ["x"], "feedback": "still weak"}', None)
        return ("", None)
    client.generate = AsyncMock(side_effect=_generate)

    result = await run_pipeline(
        initial_draft="x", content_type="tutorial", project_paths=paths, llm_client=client
    )
    # Both persona passes failed; result is flagged but still produced.
    assert result.flagged is True
    assert result.final_text  # not empty


@pytest.mark.asyncio
async def test_revision_trace_is_serializable(tmp_path):
    paths = _project(tmp_path)
    client = _mock_client_for_clean_run()
    result = await run_pipeline(
        initial_draft="x", content_type="tutorial", project_paths=paths, llm_client=client
    )
    import json
    serialized = json.dumps(result.revision_trace)
    parsed = json.loads(serialized)
    assert "stages" in parsed
    assert "content_type" in parsed
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/quality/test_editorial.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError on `devrel_swarm.quality.editorial`.

- [ ] **Step 3: Implement `editorial.py`**

Write `src/devrel_swarm/quality/editorial.py`:
```python
"""8-stage editorial pipeline orchestrator.

Stage flow:
  1. Generate (caller's responsibility — initial_draft is the input)
  2. Developmental edit  — generate_with_revision (Sonnet, min_score=7, max_rounds=2)
  3. Line edit           — generate_with_revision (Sonnet, min_score=7, max_rounds=2)
  4. Copy edit           — generate_with_revision (Sonnet, min_score=7, max_rounds=2)
  5. Anti-slop           — regex + LLM lint; on hit, one targeted rewrite;
                            on second failure, AbortLoud
  6. Persona             — Haiku score 1-10 + weak sections
  7. Readability         — pure-Python FRE/sentence-stats/jargon check
  → If 6 or 7 fail: re-run stage 4 once with the failed rubric, then
    re-run 5/6/7 once. Second failure of 6/7 logs and ships flagged.
  8. Brand audit         — Sentinel (caller's responsibility; orchestrator
                            does not invoke Sentinel because it lives in
                            core/sentinel.py and would create a quality→core
                            dependency. The agent that calls run_pipeline
                            invokes Sentinel separately.)

Returns EditorialResult with the final text, every stage's StageResult,
and a JSON-serializable revision_trace.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.quality.persona import test_against_persona
from devrel_swarm.quality.readability import check_against_target, compute_readability
from devrel_swarm.quality.slop import find_slop, force_rewrite, llm_lint, parse_blocklist
from devrel_swarm.quality.style import get_targets, load_style
from devrel_swarm.quality.voice import load_voice


class AbortLoud(Exception):
    """Raised when the slop pipeline cannot clear flagged phrases after one
    targeted rewrite. Callers should let this propagate; the message lists
    the offending phrases for diagnosis."""


@dataclass
class StageResult:
    name: str
    text_before: str
    text_after: str
    duration_s: float
    score: int | None = None
    issues: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class EditorialResult:
    final_text: str
    stages: list[StageResult]
    flagged: bool
    revision_trace: dict[str, Any]


_DEV_EDIT_SYSTEM = """You are a developmental editor. Improve the draft for:
- structure (does the opening hook? does it close cleanly?)
- argument (is each section earning its place?)
- specificity (is anything generic or hand-wavy?)

Preserve the project voice strictly. Return only the revised content.
"""

_LINE_EDIT_SYSTEM = """You are a line editor. Improve the draft for:
- sentence rhythm (vary length; avoid monotone)
- voice fidelity (match the voice contract precisely)
- word choice (specific, concrete, never vague)

Preserve structure and meaning. Return only the revised content.
"""

_COPY_EDIT_SYSTEM = """You are a copy editor. Improve the draft for:
- grammar, punctuation, agreement
- code blocks (correct syntax, language tags, working examples)
- consistency (capitalization, terminology, tense)

Make minimal changes; preserve voice. Return only the revised content.
"""


def _make_user(text: str, voice: str, style: str, content_type: str, extra: str = "") -> str:
    parts = [
        f"Content type: {content_type}",
        "",
        "Voice contract:",
        voice or "(none yet)",
        "",
        "House style:",
        style or "(none yet)",
        "",
    ]
    if extra:
        parts.extend(["Additional notes:", extra, ""])
    parts.extend(["Draft:", text])
    return "\n".join(parts)


async def _editorial_stage(
    *,
    name: str,
    system: str,
    text_before: str,
    voice: str,
    style: str,
    content_type: str,
    llm_client,
    extra: str = "",
) -> tuple[str, StageResult]:
    t0 = time.monotonic()
    user = _make_user(text_before, voice, style, content_type, extra)
    revised, trace = await llm_client.generate_with_revision(
        system_prompt=system,
        user_prompt=user,
        min_score=7,
        max_rounds=2,
    )
    final_score = getattr(trace, "final_score", None)
    rounds = getattr(trace, "revision_rounds", 0)
    return revised, StageResult(
        name=name,
        text_before=text_before,
        text_after=revised,
        duration_s=round(time.monotonic() - t0, 3),
        score=final_score,
        detail=f"rounds={rounds}",
    )


async def _slop_stage(
    *,
    text_before: str,
    blocklist: list[str],
    voice: str,
    llm_client,
) -> tuple[str, StageResult]:
    t0 = time.monotonic()
    regex_hits = find_slop(text_before, blocklist)
    lint_hits = await llm_lint(text_before, voice, llm_client)
    if not regex_hits and not lint_hits:
        return text_before, StageResult(
            name="anti_slop",
            text_before=text_before,
            text_after=text_before,
            duration_s=round(time.monotonic() - t0, 3),
            detail="clean",
        )
    rewritten = await force_rewrite(text_before, regex_hits, lint_hits, voice, llm_client)
    # Re-check after rewrite.
    re_regex = find_slop(rewritten, blocklist)
    re_lint = await llm_lint(rewritten, voice, llm_client)
    if re_regex or re_lint:
        offenders = sorted({h.phrase for h in re_regex} | set(re_lint))
        raise AbortLoud(
            "Slop persisted after rewrite: " + ", ".join(offenders)
        )
    return rewritten, StageResult(
        name="anti_slop",
        text_before=text_before,
        text_after=rewritten,
        duration_s=round(time.monotonic() - t0, 3),
        issues=sorted({h.phrase for h in regex_hits} | set(lint_hits)),
        detail="rewrite_applied",
    )


async def _persona_stage(
    *,
    text: str,
    content_type: str,
    voice: str,
    llm_client,
) -> StageResult:
    t0 = time.monotonic()
    res = await test_against_persona(
        text=text, content_type=content_type, voice=voice, llm_client=llm_client
    )
    issues = []
    if res.score < 7:
        issues.append(f"Persona score {res.score} < 7")
        if res.weak_sections:
            issues.extend(res.weak_sections)
    return StageResult(
        name="persona",
        text_before=text,
        text_after=text,
        duration_s=round(time.monotonic() - t0, 3),
        score=res.score,
        issues=issues,
        detail=res.feedback,
    )


def _readability_stage(*, text: str, content_type: str, style_md: str) -> StageResult:
    t0 = time.monotonic()
    targets = get_targets(content_type, style_md)
    scores = compute_readability(text)
    issues = check_against_target(scores, targets)
    return StageResult(
        name="readability",
        text_before=text,
        text_after=text,
        duration_s=round(time.monotonic() - t0, 3),
        issues=issues,
        detail=f"FRE={scores.flesch_reading_ease}, MSL={scores.mean_sentence_length}",
    )


async def run_pipeline(
    *,
    initial_draft: str,
    content_type: str,
    project_paths: ProjectPaths,
    llm_client,
) -> EditorialResult:
    """Run the 8-stage editorial pipeline. See module docstring."""
    voice = load_voice(project_paths)
    style_md = load_style(project_paths)
    blocklist = parse_blocklist(
        project_paths.slop_file.read_text(encoding="utf-8")
        if project_paths.slop_file.is_file()
        else ""
    )

    stages: list[StageResult] = []

    # Stages 2-4: editorial loops.
    text, sr = await _editorial_stage(
        name="developmental_edit",
        system=_DEV_EDIT_SYSTEM,
        text_before=initial_draft,
        voice=voice, style=style_md, content_type=content_type,
        llm_client=llm_client,
    )
    stages.append(sr)

    text, sr = await _editorial_stage(
        name="line_edit",
        system=_LINE_EDIT_SYSTEM,
        text_before=text,
        voice=voice, style=style_md, content_type=content_type,
        llm_client=llm_client,
    )
    stages.append(sr)

    text, sr = await _editorial_stage(
        name="copy_edit",
        system=_COPY_EDIT_SYSTEM,
        text_before=text,
        voice=voice, style=style_md, content_type=content_type,
        llm_client=llm_client,
    )
    stages.append(sr)

    # Stage 5: anti-slop. May raise AbortLoud — let it propagate.
    text, sr = await _slop_stage(
        text_before=text, blocklist=blocklist, voice=voice, llm_client=llm_client,
    )
    stages.append(sr)

    # Stage 6: persona.
    persona_sr = await _persona_stage(
        text=text, content_type=content_type, voice=voice, llm_client=llm_client,
    )
    stages.append(persona_sr)

    # Stage 7: readability.
    readability_sr = _readability_stage(text=text, content_type=content_type, style_md=style_md)
    stages.append(readability_sr)

    # Re-loop into copy-edit if either soft gate failed.
    flagged = False
    if persona_sr.issues or readability_sr.issues:
        extra = "Previous persona feedback: " + (persona_sr.detail or "")
        if readability_sr.issues:
            extra += "\nReadability issues: " + "; ".join(readability_sr.issues)
        text, sr = await _editorial_stage(
            name="copy_edit",
            system=_COPY_EDIT_SYSTEM,
            text_before=text,
            voice=voice, style=style_md, content_type=content_type,
            llm_client=llm_client,
            extra=extra,
        )
        stages.append(sr)

        # Re-run anti-slop, persona, readability one more time.
        text, sr = await _slop_stage(
            text_before=text, blocklist=blocklist, voice=voice, llm_client=llm_client,
        )
        stages.append(sr)

        persona2 = await _persona_stage(
            text=text, content_type=content_type, voice=voice, llm_client=llm_client,
        )
        stages.append(persona2)

        readability2 = _readability_stage(
            text=text, content_type=content_type, style_md=style_md
        )
        stages.append(readability2)

        if persona2.issues or readability2.issues:
            flagged = True

    revision_trace = {
        "content_type": content_type,
        "voice_present": bool(voice),
        "style_present": bool(style_md),
        "blocklist_size": len(blocklist),
        "stages": [asdict(s) for s in stages],
        "flagged": flagged,
    }

    return EditorialResult(
        final_text=text,
        stages=stages,
        flagged=flagged,
        revision_trace=revision_trace,
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/quality/test_editorial.py -v --no-cov 2>&1 | tail -15
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/quality/editorial.py tests/quality/test_editorial.py
git commit -m "feat(quality): add 8-stage editorial pipeline orchestrator"
```

---

## Task 8: Integrate quality pipeline into Kai

**Files:**
- Modify: `src/devrel_swarm/core/kai.py:295-312` (the `generate_with_revision` call site)
- Modify: `tests/test_kai.py` (the existing test that mocks the call)

The existing call site in `kai.py`:
```python
content, trace = await self.llm_client.generate_with_revision(
    system_prompt=...,
    user_prompt=...,
    ...
)
# ... use trace.critiques[-1].strengths / .issues
```

The new call replaces this with `run_pipeline`. Trace usage changes — we use `EditorialResult.stages` instead of a single critique trace.

- [ ] **Step 1: Read the existing call site**

```bash
sed -n '280,330p' src/devrel_swarm/core/kai.py
```
Locate the `generate_with_revision` invocation around line 295. Read the surrounding ~30 lines to understand what `trace.critiques[-1]` is consumed for (likely populating a `ContentPiece` dataclass).

- [ ] **Step 2: Plan the surgical edit**

The replacement needs to:
1. Call `run_pipeline` instead of `generate_with_revision`.
2. Read the project root via `find_devrel_root`. If no project (i.e., no `.devrel/`), fall back to the current behavior. This preserves backward compat for callers that haven't run `devrel init`.
3. Map `EditorialResult.stages` to the same data the agent currently extracts (strengths/issues from the last critique). For now, summarize: take any `issues` field across stages as `issues`, leave `strengths` empty (the new pipeline doesn't have a single "strengths" output; use the persona's `feedback` if non-empty, else "Pipeline complete").

- [ ] **Step 3: Apply the edit**

In `src/devrel_swarm/core/kai.py`, around line 295, replace the `generate_with_revision` call with a fallback-aware `run_pipeline` call. The exact replacement (preserving surrounding code as-is, only the LLM-call block changes):

```python
# Find the existing block:
#     content, trace = await self.llm_client.generate_with_revision(
#         system_prompt=...,
#         user_prompt=...,
#         ...
#     )
# Replace ENTIRELY with:

from devrel_swarm.project.paths import ProjectNotFoundError, find_devrel_root, ProjectPaths
from devrel_swarm.quality.editorial import AbortLoud, run_pipeline

# Determine content_type from the kai task (defaults to tutorial).
content_type = getattr(self, "_content_type", "tutorial")

# Generate the initial draft using a single non-revising LLM call. This
# replaces the legacy "draft → critique → revise" inside generate_with_revision
# with the editorial pipeline taking over after the initial draft.
draft, _ = await self.llm_client.generate(
    system_prompt=system_prompt,
    user_prompt=user_prompt,
)

try:
    paths = ProjectPaths.from_root(find_devrel_root())
    result = await run_pipeline(
        initial_draft=draft,
        content_type=content_type,
        project_paths=paths,
        llm_client=self.llm_client,
    )
    content = result.final_text
    # Map stages → strengths/issues for the legacy ContentPiece interface.
    strengths = [result.stages[-1].detail] if result.stages else []
    issues = [issue for s in result.stages for issue in s.issues]
except (ProjectNotFoundError, AbortLoud) as e:
    # Fall back to the legacy single-revision-loop behaviour when there's
    # no .devrel/ project or the pipeline aborted on slop. Logged for
    # visibility.
    logger.warning("editorial pipeline unavailable, using single-revision: %s", e)
    content, trace = await self.llm_client.generate_with_revision(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        min_score=7,
        max_rounds=2,
    )
    strengths = trace.critiques[-1].strengths if trace.critiques else []
    issues = trace.critiques[-1].issues if trace.critiques else []
```

Use the Edit tool to apply this. The exact `old_string` and `new_string` depend on what's currently in `kai.py:295-312`; capture the existing block first via `Read`, then `Edit`.

The `from ... import` lines should be moved to the top of the file with the other imports — do not leave them inline. After the edit, `from devrel_swarm.project.paths` and `from devrel_swarm.quality.editorial` should appear in the import block at the top of `kai.py`.

- [ ] **Step 4: Update `tests/test_kai.py` to match the new call surface**

The existing test mocks `generate_with_revision`. The new code path also calls `generate` (for the initial draft) and the editorial primitives. For Phase 3, simplify by patching at the `run_pipeline` level for any test that exercises the new path. Tests that don't go through the project-root-discovery branch (i.e., they run without a `.devrel/` and trigger the fallback) keep mocking `generate_with_revision`.

```bash
grep -n "generate_with_revision\|generate(" tests/test_kai.py | head -20
```

For each test that currently mocks `generate_with_revision` for Kai's content-generation path:
- If the test uses a `tmp_path` with `.devrel/` already initialized (or you can add one), patch `run_pipeline` directly.
- If the test just wants a quick draft, patch `generate` + ensure no `.devrel/` exists in the test's working dir.

Use the existing test patterns. Aim to keep tests passing without changing assertions where possible — only update mock targets.

If you find a test that's testing internal critique behavior that no longer applies (e.g., it asserts on `trace.critiques[-1].strengths`), update the assertion to check the new `strengths` list (set to `[StageResult.detail]` per the integration code above).

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tail -10
grep "^FAILED" <(python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1) | sort > /tmp/pytest.failures.kai.after.txt
diff /tmp/pytest.failures.phase3.before.txt /tmp/pytest.failures.kai.after.txt
```

If new failures appear (anything beyond the locked 22), fix them by updating the mocks per Step 4 — do NOT alter test assertions to make them pass without understanding why they failed.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_swarm/core/kai.py tests/test_kai.py
git commit -m "feat(kai): integrate quality pipeline into content generation"
```

---

## Task 9: Integrate quality pipeline into Mox

**Files:**
- Modify: `src/devrel_swarm/core/mox.py:425` (the `generate_with_revision` call site)
- Modify: `tests/test_mox.py`

Mirror Task 8 exactly, but for Mox. The call site in `mox.py:425` is:
```python
raw, trace = await self.llm_client.generate_with_revision(...)
```

Apply the same surgical replacement pattern. Mox has multiple content types (`blog_post`, `landing_page`, `social`, `campaign_brief`); detect the active one from method context and pass it as `content_type` to `run_pipeline`.

- [ ] **Step 1: Inspect call site + content-type variants**

```bash
grep -n "def execute_\|generate_with_revision\|content_type" src/devrel_swarm/core/mox.py | head -20
```

Determine which method is calling `generate_with_revision` and what content type it produces. Mox has methods like `_generate_blog_post`, `_generate_landing_page`, etc. — each should pass its own `content_type`.

- [ ] **Step 2: Apply the surgical edit**

Replace the `generate_with_revision` block with the same pattern as Kai (initial `generate` → `run_pipeline` → fallback). Pass the right `content_type` per call site. If multiple call sites exist in Mox, refactor them through a common helper:

```python
# At the bottom of mox.py, add a helper method:
async def _generate_with_pipeline(
    self,
    *,
    system_prompt: str,
    user_prompt: str,
    content_type: str,
) -> tuple[str, list[str], list[str]]:
    """Returns (final_text, strengths, issues). Falls back to legacy
    revision loop when no .devrel/ project or pipeline aborts."""
    from devrel_swarm.project.paths import ProjectNotFoundError, find_devrel_root, ProjectPaths
    from devrel_swarm.quality.editorial import AbortLoud, run_pipeline

    draft, _ = await self.llm_client.generate(
        system_prompt=system_prompt, user_prompt=user_prompt,
    )
    try:
        paths = ProjectPaths.from_root(find_devrel_root())
        result = await run_pipeline(
            initial_draft=draft,
            content_type=content_type,
            project_paths=paths,
            llm_client=self.llm_client,
        )
        strengths = [result.stages[-1].detail] if result.stages else []
        issues = [i for s in result.stages for i in s.issues]
        return result.final_text, strengths, issues
    except (ProjectNotFoundError, AbortLoud) as e:
        logger.warning("mox: editorial pipeline unavailable, using single-revision: %s", e)
        content, trace = await self.llm_client.generate_with_revision(
            system_prompt=system_prompt, user_prompt=user_prompt,
            min_score=7, max_rounds=2,
        )
        strengths = trace.critiques[-1].strengths if trace.critiques else []
        issues = trace.critiques[-1].issues if trace.critiques else []
        return content, strengths, issues
```

Then each call site becomes one line: `text, strengths, issues = await self._generate_with_pipeline(system_prompt=..., user_prompt=..., content_type="blog_post")`.

Place the imports at the top of `mox.py`, not inside the helper.

- [ ] **Step 3: Update `tests/test_mox.py`**

Same approach as Kai: patch `run_pipeline` (or `_generate_with_pipeline`) at the test level to keep the existing assertions clean. Where tests assert on legacy `trace.critiques`, update to check the new strengths/issues lists.

- [ ] **Step 4: Run full suite, confirm baseline**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tail -5
diff /tmp/pytest.failures.phase3.before.txt <(python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort)
```
Expected: empty diff (still 22 known failures).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/mox.py tests/test_mox.py
git commit -m "feat(mox): integrate quality pipeline via _generate_with_pipeline helper"
```

---

## Task 10: Integrate quality pipeline into Pax

**Files:**
- Modify: `src/devrel_swarm/core/pax.py:1089`
- Modify: `tests/test_pax.py`

Apply the same pattern as Mox. Pax has multiple content surfaces too (`outreach_email`, `battle_card`, `nurture_sequence`); pass the right `content_type` per call site.

- [ ] **Step 1: Inspect**

```bash
grep -n "def \|generate_with_revision\|content_type" src/devrel_swarm/core/pax.py | head -30
```

- [ ] **Step 2: Apply surgical edit using the same `_generate_with_pipeline` helper pattern**

Add the helper to `pax.py` (the same code as Mox's helper, with logger renamed appropriately if needed). Replace each `generate_with_revision` call with a call to the helper passing the appropriate `content_type` (`"cold_email"` for outreach, `"battle_card"` for battle cards).

- [ ] **Step 3: Update `tests/test_pax.py`** with the same approach as Tasks 8-9.

- [ ] **Step 4: Run full suite, verify parity**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tail -5
diff /tmp/pytest.failures.phase3.before.txt <(python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort)
```
Expected: empty diff.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/pax.py tests/test_pax.py
git commit -m "feat(pax): integrate quality pipeline via _generate_with_pipeline helper"
```

---

## Task 11: `devrel content draft` CLI command

**Files:**
- Create: `src/devrel_swarm/cli/content.py`
- Create: `tests/cli/test_content_command.py`
- Modify: `src/devrel_swarm/cli/__init__.py` (register the `content` typer subapp)

`devrel content draft <prompt>` is the human-facing entry point to the quality pipeline. It generates an initial draft via a single LLM call, runs `run_pipeline`, writes the final text to `.devrel/deliverables/`, writes the revision trace to `.devrel/deliverables/<slug>-trace.json`, and prints a Rich summary.

- [ ] **Step 1: Write failing test**

Write `tests/cli/test_content_command.py`:
```python
"""Tests for `devrel content draft` and `devrel content audit`."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init_project(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


@patch("devrel_swarm.cli.content._build_llm_client")
@patch("devrel_swarm.cli.content.run_pipeline")
def test_draft_writes_deliverable_and_trace(mock_pipeline, mock_client, tmp_path):
    _init_project(tmp_path)
    mock_pipeline.return_value = MagicMock(
        final_text="Final clean draft.",
        flagged=False,
        stages=[],
        revision_trace={"content_type": "tutorial", "stages": []},
    )
    mock_pipeline.side_effect = None
    # Make run_pipeline awaitable
    async def _runner(**kwargs):
        return mock_pipeline.return_value
    import devrel_swarm.cli.content as content_mod
    content_mod.run_pipeline = AsyncMock(return_value=mock_pipeline.return_value)
    mock_client.return_value = MagicMock(generate=AsyncMock(return_value=("initial draft", None)))

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            [
                "content", "draft", "tutorial on feature flags",
                "--type", "tutorial",
            ],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    deliverables = list((tmp_path / ".devrel" / "deliverables").glob("*.md"))
    traces = list((tmp_path / ".devrel" / "deliverables").glob("*-trace.json"))
    assert len(deliverables) == 1
    assert len(traces) == 1
    assert "Final clean draft." in deliverables[0].read_text()
    trace = json.loads(traces[0].read_text())
    assert trace["content_type"] == "tutorial"


def test_draft_fails_without_project(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app,
            ["content", "draft", "x", "--type", "tutorial"],
            env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0


def test_audit_runs_pipeline_against_existing_file(tmp_path):
    _init_project(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("This is a draft about feature flags.")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli.content.run_pipeline", new=AsyncMock(return_value=MagicMock(
            final_text="rewritten",
            flagged=False, stages=[],
            revision_trace={"content_type": "tutorial", "stages": []},
        ))):
            with patch("devrel_swarm.cli.content._build_llm_client", return_value=MagicMock(
                generate=AsyncMock(return_value=("x", None))
            )):
                result = runner.invoke(
                    app,
                    ["content", "audit", str(draft), "--type", "tutorial"],
                    env={"ANTHROPIC_API_KEY": "sk-ant-test", **os.environ},
                )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert "rewritten" in result.output or "rewritten" in (tmp_path / ".devrel" / "deliverables").glob("*.md").__iter__().__next__().read_text()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/cli/test_content_command.py -v --no-cov 2>&1 | tail -5
```
Expected: ImportError (no `cli/content.py` yet).

- [ ] **Step 3: Implement `cli/content.py`**

Write `src/devrel_swarm/cli/content.py`:
```python
"""`devrel content draft|audit` — primary entry points to the editorial pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectNotFoundError, ProjectPaths, find_devrel_root
from devrel_swarm.quality.editorial import AbortLoud, run_pipeline

console = Console()

content_app = typer.Typer(
    name="content",
    help="Generate and audit content through the editorial quality pipeline.",
    no_args_is_help=True,
    add_completion=False,
)


def _build_llm_client() -> LLMClient:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise typer.BadParameter("ANTHROPIC_API_KEY is required.")
    return LLMClient(api_key=api_key)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "draft"


def _write_outputs(paths: ProjectPaths, slug: str, body: str, trace: dict) -> tuple[Path, Path]:
    paths.deliverables_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    body_path = paths.deliverables_dir / f"{ts}-{slug}.md"
    trace_path = paths.deliverables_dir / f"{ts}-{slug}-trace.json"
    body_path.write_text(body)
    trace_path.write_text(json.dumps(trace, indent=2))
    return body_path, trace_path


@content_app.command("draft")
def draft_command(
    prompt: str = typer.Argument(..., help="Topic or instruction for the new content."),
    content_type: str = typer.Option(
        "tutorial", "--type",
        help="Content type for targeting (tutorial, blog_post, landing_page, cold_email, battle_card).",
    ),
) -> None:
    """Generate new content via the 8-stage editorial pipeline."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    paths = ProjectPaths.from_root(root)
    client = _build_llm_client()

    async def _do() -> None:
        draft, _ = await client.generate(
            system_prompt=(
                "You are a writer producing a first draft. Stay specific and concrete. "
                "Avoid marketing fluff."
            ),
            user_prompt=prompt,
        )
        try:
            result = await run_pipeline(
                initial_draft=draft,
                content_type=content_type,
                project_paths=paths,
                llm_client=client,
            )
        except AbortLoud as e:
            console.print(f"[red]Pipeline aborted: {e}[/red]")
            raise typer.Exit(code=1) from None
        body_path, trace_path = _write_outputs(paths, _slug(prompt), result.final_text, result.revision_trace)
        console.print(f"[green]✓[/green] Wrote {body_path.name} ({len(result.final_text)} chars)")
        console.print(f"[green]✓[/green] Wrote {trace_path.name}")
        if result.flagged:
            console.print("[yellow]⚠[/yellow] Flagged: persona or readability gates failed twice; output shipped anyway.")

    asyncio.run(_do())


@content_app.command("audit")
def audit_command(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Existing draft to audit."),
    content_type: str = typer.Option("tutorial", "--type"),
) -> None:
    """Run the editorial pipeline against an existing draft file."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    paths = ProjectPaths.from_root(root)
    client = _build_llm_client()

    async def _do() -> None:
        body = file.read_text()
        try:
            result = await run_pipeline(
                initial_draft=body,
                content_type=content_type,
                project_paths=paths,
                llm_client=client,
            )
        except AbortLoud as e:
            console.print(f"[red]Pipeline aborted: {e}[/red]")
            raise typer.Exit(code=1) from None
        body_path, trace_path = _write_outputs(paths, _slug(file.stem), result.final_text, result.revision_trace)
        console.print(f"[green]✓[/green] Wrote {body_path.name}")
        console.print(f"[green]✓[/green] Wrote {trace_path.name}")
        if result.flagged:
            console.print("[yellow]⚠[/yellow] Flagged.")

    asyncio.run(_do())
```

- [ ] **Step 4: Register the `content` subapp in `cli/__init__.py`**

In `src/devrel_swarm/cli/__init__.py`, add:
```python
from devrel_swarm.cli.content import content_app
```
And after the `app.command(name="doctor")(doctor_command)` line:
```python
app.add_typer(content_app, name="content")
```

- [ ] **Step 5: Run tests to verify pass**

```bash
python -m pytest tests/cli/test_content_command.py -v --no-cov 2>&1 | tail -10
```
Expected: 3 passed.

- [ ] **Step 6: Smoke test**

```bash
T=$(mktemp -d) && cd "$T" && \
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name smoke --url "" --github-repo "" >/dev/null && \
echo "draft.md content with 'tapestry' slop" > draft.md && \
ls .devrel/deliverables/ 2>/dev/null && \
echo "init scaffold ready" && \
cd - && rm -rf "$T"
```
Expected: prints `init scaffold ready`. (We don't run the actual `content draft` here because it'd hit the real API; that's covered by the unit tests.)

- [ ] **Step 7: Commit**

```bash
git add src/devrel_swarm/cli/content.py src/devrel_swarm/cli/__init__.py tests/cli/test_content_command.py
git commit -m "feat(cli): add 'devrel content draft|audit' commands"
```

---

## Task 12: Verify, document, finalize

**Files:**
- Modify: `CLAUDE.md` (Phase 3 commands + File Map entries)

- [ ] **Step 1: Run the full test suite**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.phase3.after.txt | tail -10
grep "^FAILED" /tmp/pytest.phase3.after.txt | sort > /tmp/pytest.failures.phase3.after.txt
diff /tmp/pytest.failures.phase3.before.txt /tmp/pytest.failures.phase3.after.txt
```
Expected: empty diff. The summary line should read approximately **`643 passed, 22 failed`** (598 baseline + ~45 new tests across quality and cli/content; exact count varies).

- [ ] **Step 2: Coverage check on new packages**

```bash
python -m pytest tests/quality tests/cli/test_content_command.py \
  --cov=devrel_swarm.quality --cov=devrel_swarm.cli.content \
  --cov-report=term-missing 2>&1 | tail -25
```
Expected: ≥80% on `devrel_swarm.quality` and on `devrel_swarm.cli.content`. If anything is below, add tests for the uncovered branches.

- [ ] **Step 3: Update `CLAUDE.md`**

In `CLAUDE.md` `## Commands` section, add:
```bash
# Generate content via the 8-stage editorial pipeline
devrel content draft "tutorial on feature flags" --type tutorial

# Audit an existing draft
devrel content audit ./draft.md --type blog_post
```

In the File Map, add after the `src/devrel_swarm/project/` block:
```
src/devrel_swarm/quality/  8-stage editorial pipeline. voice.py loads
                           voice.md; style.py loads + parses targets;
                           slop.py runs regex + LLM lint + force-rewrite;
                           persona.py scores via skeptical-dev persona;
                           readability.py computes Flesch + sentence
                           stats; editorial.py orchestrates the 8 stages
                           with copy-edit fallback on persona/readability
                           failures.
```

In `## Coding Conventions`, add:
```
- New content-producing agents must call `quality.editorial.run_pipeline`,
  not `generate_with_revision` directly. The single legacy revision loop
  is for fallback only (no .devrel/ project, or pipeline AbortLoud).
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Phase 3 commands and quality/ File Map entry"
```

- [ ] **Step 5: Verify final state**

```bash
git log --oneline main..HEAD
devrel --version
```
Expected: a stack of focused commits on `feat/cli-phase3-quality`, `devrel-swarm 0.2.0`.

---

## Self-review checklist (already applied)

- **Spec coverage:** all 5 quality levers (voice/style/slop/persona/readability) implemented as their own modules; 8-stage editorial pipeline orchestrates them; `run_pipeline` is the single entry point per spec; integrated into Kai/Mox/Pax (Vox excluded per spec correction noted at top); `devrel content draft|audit` exposes the pipeline.
- **No placeholders:** every step has either explicit code or a verification command. Where a sed/Edit operation is highly file-specific (Tasks 8-10), the plan describes the surgical change with the surrounding context required to apply it.
- **Type / name consistency:** `run_pipeline`, `EditorialResult`, `StageResult`, `AbortLoud`, `ContentTypeTargets`, `PersonaResult`, `ReadabilityScores`, `SlopHit`, `find_slop`, `llm_lint`, `force_rewrite`, `parse_blocklist`, `compute_readability`, `check_against_target`, `load_voice`, `load_style`, `parse_targets`, `get_targets`, `test_against_persona` — used consistently across tasks.
- **Cost trade-off honored:** Haiku for slop lint + persona; Sonnet for editorial stages + force-rewrite; readability is local. Aligns with the spec's "~2.5–4× spend" expectation.

## Out of scope (deferred to later phases)

- Full CLI surface beyond `init`/`doctor`/`content` (Phase 4): `devrel run`, `devrel triage`, `devrel listen`, `devrel sales`, `devrel marketing`, `devrel intel`, `devrel kb`, `devrel schedule`, `devrel cost`, `devrel deliverables`, `devrel ask`, `devrel docs`, `devrel video`, `devrel synthesize`, `devrel experiment`.
- Wiring agent cost-tracking to `.devrel/state.db` `costs` table — Phase 4 (along with `devrel cost`).
- BudgetGate enforcement (the cap; warning only is acceptable in Phase 3).
- Archiving `product/v0-agentic-alpha` branch — Phase 5.
- Refactoring Sentinel into the editorial pipeline (currently agents call Sentinel separately after `run_pipeline`).
