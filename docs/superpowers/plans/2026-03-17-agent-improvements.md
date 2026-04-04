# Agent System Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 29 pre-existing test failures, eliminate ~300 lines of duplication via BaseAgent + KnowledgeBaseSearch, add edge-case tests, parallelize weekly cycle, type agent returns, add per-agent config, and add structured logging with token cost tracking.

**Architecture:** Extract shared KB search and markdown utilities into `agents/base.py`. Type agent `execute()` returns as TypedDicts. Add per-agent config stanzas to `AgentConfig`. Wrap LLM calls with cost tracking. Fix PostHog→OpenClaw rename in tests.

**Tech Stack:** Python 3.12+, pytest, pytest-asyncio, asyncio.gather, TypedDict, dataclasses, logging

---

## Chunk 1: Fix Pre-existing Test Failures (PostHog→OpenClaw Rename)

### Task 1: Fix test_search_tools.py (7 failures)

**Files:**
- Modify: `tests/test_search_tools.py`
- Reference: `tools/search_tools.py`

The production code was renamed from `search_posthog_docs` → `search_openclaw_docs` and URLs changed from `posthog.com` → `openclaw.ai`, but the tests still use old names.

- [ ] **Step 1: Read current test file to identify all old references**

Run: `grep -n "posthog\|PostHog" tests/test_search_tools.py | head -40`

- [ ] **Step 2: Update method references**

Replace all `search_posthog_docs` → `search_openclaw_docs` in tests.

Replace class name `TestSearchPosthogDocs` → `TestSearchOpenclawDocs`.

- [ ] **Step 3: Update URL mocks**

Replace all `posthog.com` → `openclaw.ai` in respx route patterns and assertions.

Replace `docs.posthog.com` → `docs.openclaw.ai` or whatever the production code now uses.

Check the production `search_tools.py` for the exact URLs and match them.

- [ ] **Step 4: Update discourse URLs if present**

Replace `community.posthog.com` → `community.openclaw.ai` (or whatever the production code uses).

- [ ] **Step 5: Run tests to verify fixes**

Run: `python3 -m pytest tests/test_search_tools.py -v`
Expected: All 30 tests PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_search_tools.py
git commit -m "fix(tests): update search_tools tests for OpenClaw rename"
```

---

### Task 2: Fix test_sage.py product area detection (5 failures)

**Files:**
- Modify: `tests/test_sage.py`
- Reference: `agents/sage.py` (specifically `_detect_product_area` method)

The product area keywords were updated for OpenClaw (e.g., "agents" is now a product area) but the test expectations still assert old PostHog product areas.

- [ ] **Step 1: Read the production _detect_product_area method**

Read `agents/sage.py` and find the `_detect_product_area` method. Note the exact keyword→area mapping.

- [ ] **Step 2: Read the failing test assertions**

Read `tests/test_sage.py::TestSageProductAreaDetection` to see what inputs and expected outputs are used.

- [ ] **Step 3: Update test inputs and expectations to match current product areas**

For each failing test, either:
- Change the test input string so it matches the expected area, OR
- Change the expected area to match what the production code returns for that input

The goal is to test the CURRENT product area logic, not the old PostHog one.

- [ ] **Step 4: Run tests to verify**

Run: `python3 -m pytest tests/test_sage.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_sage.py
git commit -m "fix(tests): update sage product area tests for OpenClaw domains"
```

---

### Task 3: Fix test_mcp_server.py (3 failures)

**Files:**
- Modify: `tests/test_mcp_server.py`
- Reference: `tools/mcp_server.py`

Tool names and handler names changed during the rename.

- [ ] **Step 1: Read production mcp_server.py to find current tool names**

Look at `_register_tools()` to see the exact tool names registered.

- [ ] **Step 2: Read failing tests**

Read `tests/test_mcp_server.py` for the 3 failing test methods.

- [ ] **Step 3: Update tool names and handler references in tests**

Replace old PostHog tool names with current OpenClaw ones. Match exact tool names from production code.

- [ ] **Step 4: Run tests to verify**

Run: `python3 -m pytest tests/test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_mcp_server.py
git commit -m "fix(tests): update MCP server tests for renamed tools"
```

---

### Task 4: Fix test_github_tools.py (3 failures)

**Files:**
- Modify: `tests/test_github_tools.py`
- Reference: `tools/github_tools.py`

The OWNER/REPO constants changed from PostHog org to OpenClaw.

- [ ] **Step 1: Read production github_tools.py for current OWNER/REPO**

Find the constants or URLs used by GitHubTools.

- [ ] **Step 2: Update respx mocks to use current repo URLs**

Match the mock URL patterns to the production code's actual GitHub API URLs.

- [ ] **Step 3: Run tests to verify**

Run: `python3 -m pytest tests/test_github_tools.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_github_tools.py
git commit -m "fix(tests): update GitHub tools tests for OpenClaw repo URLs"
```

---

### Task 5: Fix test_vox.py (1 failure) and test_kai.py (1 failure)

**Files:**
- Modify: `tests/test_vox.py`
- Modify: `tests/test_kai.py`
- Reference: `agents/vox.py`, `agents/kai.py`

- [ ] **Step 1: Read and fix test_vox.py::TestScriptParser::test_parse_task_string**

Read the failing test. The assertion likely checks for old brand references. Update to match current OpenClaw product context.

- [ ] **Step 2: Read and fix test_kai.py::TestKaiKnowledgeBase::test_search_no_results**

Read the failing test. Update assertion to match current behavior.

- [ ] **Step 3: Run tests to verify both**

Run: `python3 -m pytest tests/test_vox.py tests/test_kai.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_vox.py tests/test_kai.py
git commit -m "fix(tests): update vox and kai tests for OpenClaw context"
```

---

### Task 6: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: `0 failed, 401 passed` (or similar — 0 failures)

- [ ] **Step 2: Commit if any stragglers were missed**

---

## Chunk 2: Extract BaseAgent + KnowledgeBaseSearch

### Task 7: Create agents/base.py with shared utilities

**Files:**
- Create: `agents/base.py`

Extract duplicated code from rex.py, pax.py, mox.py, kai.py, and iris.py into a shared module.

- [ ] **Step 1: Write the failing test for KnowledgeBaseSearch**

```python
# tests/test_base_agent.py
"""Tests for BaseAgent and KnowledgeBaseSearch."""

import pytest
from pathlib import Path
from agents.base import KnowledgeBaseSearch, strip_markdown_fences


class TestKnowledgeBaseSearch:
    """Test shared knowledge base search functionality."""

    def test_build_index(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        assert len(kb.index) > 0

    def test_build_index_nonexistent_path(self, tmp_path):
        kb = KnowledgeBaseSearch(tmp_path / "nonexistent")
        assert kb.index == {}

    def test_search_finds_matching_docs(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("python sdk installation")
        assert len(results) > 0
        assert all("source" in r and "content" in r for r in results)

    def test_search_no_results(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("xyznonexistent")
        assert results == []

    def test_search_respects_limit(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("sdk", limit=1)
        assert len(results) <= 1


class TestStripMarkdownFences:
    """Test markdown fence stripping utility."""

    def test_strips_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_strips_plain_fence(self):
        text = '```\nsome text\n```'
        assert strip_markdown_fences(text) == "some text"

    def test_no_fence_unchanged(self):
        text = '{"key": "value"}'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_strips_and_trims(self):
        text = '  ```json\n  {"a": 1}\n  ```  '
        result = strip_markdown_fences(text)
        assert "```" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_base_agent.py -v`
Expected: FAIL (agents/base.py does not exist yet)

- [ ] **Step 3: Implement agents/base.py**

```python
"""Shared base classes and utilities for all agents."""

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Common stop words excluded from KB keyword matching
STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "my", "your", "his", "its", "our", "their", "this", "that", "these",
    "those", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "same", "so", "than", "too", "very",
    "just", "because", "as", "until", "while", "of", "at", "by", "for",
    "with", "about", "against", "between", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "in", "out",
    "on", "off", "over", "under", "again", "further", "then", "once",
    "and", "but", "or", "nor", "if", "else",
})


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:json|python|text)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


class KnowledgeBaseSearch:
    """Reusable knowledge base indexer and searcher.

    Usage:
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("feature flags setup", limit=5)
    """

    def __init__(self, knowledge_base_path: Path):
        self.path = knowledge_base_path
        self.index = self._build_index()

    def _build_index(self) -> dict[str, Path]:
        """Index all markdown files in the knowledge base."""
        index: dict[str, Path] = {}
        if self.path.exists():
            for file in self.path.rglob("*.md"):
                key = file.stem.lower().replace("-", " ").replace("_", " ")
                index[key] = file
        logger.info(f"KB indexed {len(index)} documents")
        return index

    def search(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search the knowledge base by keyword matching.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with 'source' (relative path) and 'content' keys.
        """
        keywords = {
            w.lower()
            for w in re.split(r"\W+", query)
            if w.lower() not in STOP_WORDS and len(w) > 2
        }
        if not keywords:
            return []

        scored: list[tuple[int, str, str]] = []
        for key, path in self.index.items():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            content_lower = content.lower()
            score = sum(1 for kw in keywords if kw in key or kw in content_lower)
            if score > 0:
                source = str(path.relative_to(self.path))
                scored.append((score, source, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"source": source, "content": content}
            for _, source, content in scored[:limit]
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_base_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agents/base.py tests/test_base_agent.py
git commit -m "feat: extract KnowledgeBaseSearch and strip_markdown_fences into agents/base.py"
```

---

### Task 8: Refactor rex.py to use agents/base.py

**Files:**
- Modify: `agents/rex.py`

- [ ] **Step 1: Import from base and remove duplicated code**

Add: `from agents.base import KnowledgeBaseSearch, strip_markdown_fences`

Replace `self._kb_index = self._build_kb_index()` with `self._kb = KnowledgeBaseSearch(knowledge_base_path)`.

Remove the `_build_kb_index()` method entirely.

Update all references from `self._kb_index` to `self._kb.index`.

Replace calls to `self._search_knowledge_base(query)` with `self._kb.search(query)`.

Remove `_search_knowledge_base()` method.

Replace inline `_strip_markdown_fences()` with the imported one.

- [ ] **Step 2: Run existing rex tests**

Run: `python3 -m pytest tests/test_rex.py -v`
Expected: All 18 tests PASS

- [ ] **Step 3: Commit**

```bash
git add agents/rex.py
git commit -m "refactor: rex.py uses shared KnowledgeBaseSearch"
```

---

### Task 9: Refactor pax.py to use agents/base.py

**Files:**
- Modify: `agents/pax.py`

- [ ] **Step 1: Same refactoring as rex.py**

Import from base. Replace `_build_kb_index`, `_search_knowledge_base` with `KnowledgeBaseSearch`. Update all references.

- [ ] **Step 2: Run existing pax tests**

Run: `python3 -m pytest tests/test_pax.py -v`
Expected: All 17 tests PASS

- [ ] **Step 3: Commit**

```bash
git add agents/pax.py
git commit -m "refactor: pax.py uses shared KnowledgeBaseSearch"
```

---

### Task 10: Refactor mox.py to use agents/base.py

**Files:**
- Modify: `agents/mox.py`

Same pattern as Tasks 8-9.

- [ ] **Step 1: Refactor**

Import from base. Replace `_build_kb_index`, `_search_knowledge_base` with `KnowledgeBaseSearch`. Update all references.

- [ ] **Step 2: Run existing mox tests**

Run: `python3 -m pytest tests/test_mox.py -v`
Expected: All 21 tests PASS

- [ ] **Step 3: Commit**

```bash
git add agents/mox.py
git commit -m "refactor: mox.py uses shared KnowledgeBaseSearch"
```

---

### Task 11: Refactor kai.py to use agents/base.py

**Files:**
- Modify: `agents/kai.py`

Kai uses `search_knowledge_base` (public, not private). Refactor to use `KnowledgeBaseSearch`.

- [ ] **Step 1: Refactor**

Import `KnowledgeBaseSearch` from base. Replace `_build_kb_index()` and `search_knowledge_base()` with `self._kb = KnowledgeBaseSearch(...)` and `self._kb.search(...)`.

- [ ] **Step 2: Run existing kai tests**

Run: `python3 -m pytest tests/test_kai.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add agents/kai.py
git commit -m "refactor: kai.py uses shared KnowledgeBaseSearch"
```

---

### Task 12: Refactor iris.py to use strip_markdown_fences from base

**Files:**
- Modify: `agents/iris.py`

- [ ] **Step 1: Replace local _strip_markdown_fences with import**

Add: `from agents.base import strip_markdown_fences`

Remove the local `_strip_markdown_fences()` function definition.

Update callers from `_strip_markdown_fences(...)` to `strip_markdown_fences(...)`.

- [ ] **Step 2: Run existing iris tests**

Run: `python3 -m pytest tests/test_iris.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add agents/iris.py
git commit -m "refactor: iris.py uses shared strip_markdown_fences"
```

---

## Chunk 3: Parallelize Weekly Cycle + Type Returns

### Task 13: Parallelize independent stages in run_weekly_cycle

**Files:**
- Modify: `agents/atlas.py`
- Create: `tests/test_atlas_parallel.py`

- [ ] **Step 1: Write failing test for parallel execution**

```python
# tests/test_atlas_parallel.py
"""Tests for Atlas parallel stage execution."""

import asyncio
import pytest
from agents.atlas import Atlas


class TestAtlasParallelExecution:
    """Verify independent stages run concurrently."""

    @pytest.mark.asyncio
    async def test_sage_echo_run_concurrently(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        """Sage and Echo should run in parallel (no data dependency)."""
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        timestamps = {}
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name, task, context=None):
            timestamps[f"{agent_name}_start"] = asyncio.get_event_loop().time()
            result = await original_delegate(agent_name, task, context)
            timestamps[f"{agent_name}_end"] = asyncio.get_event_loop().time()
            return result

        atlas.delegate = tracking_delegate
        await atlas.run_weekly_cycle()

        # If sage and echo run in parallel, echo_start should be before sage_end
        # (or very close to sage_start)
        assert "sage_start" in timestamps
        assert "echo_start" in timestamps
        # Echo should start before sage finishes (parallel)
        assert timestamps["echo_start"] < timestamps["sage_end"] + 0.1
```

- [ ] **Step 2: Run test to verify it fails (currently sequential)**

Run: `python3 -m pytest tests/test_atlas_parallel.py -v`
Expected: FAIL (echo starts after sage finishes)

- [ ] **Step 3: Refactor run_weekly_cycle to use asyncio.gather**

In `agents/atlas.py`, modify `run_weekly_cycle()`:

Stage 1: `sage` + `echo` run in parallel via `asyncio.gather()`
Stage 2: `rex` runs after stage 1 (needs echo+sage context)
Stage 3: `iris` runs after rex (needs sage+echo+rex context)
Stage 4: `nova` runs after iris
Stage 5: `dex` + `kai` could be parallel (dex doesn't need kai, but kai needs dex — so dex first, then kai)
Stage 6: `vox` after kai
Stage 7: OKR compilation

```python
# Stage 1: Sage + Echo in parallel (no dependencies)
sage_result, echo_result = await asyncio.gather(
    self.delegate("sage", "Triage GitHub issues..."),
    self.delegate("echo", "Scan Reddit, HN, Twitter/X..."),
)
if sage_result.success:
    self.context.sage_triage = sage_result.output
if echo_result.success:
    self.context.echo_social = echo_result.output

# Stage 2: Rex (uses sage + echo context)
rex_result = await self.delegate("rex", "Analyze competitive landscape...")
if rex_result.success:
    self.context.rex_competitive = rex_result.output

# Stage 3: Iris (uses sage + echo + rex context)
# ... etc
```

- [ ] **Step 4: Run tests to verify**

Run: `python3 -m pytest tests/test_atlas_parallel.py tests/test_atlas.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agents/atlas.py tests/test_atlas_parallel.py
git commit -m "feat: parallelize independent stages in weekly cycle with asyncio.gather"
```

---

### Task 14: Type SharedContext fields and agent return types

**Files:**
- Create: `agents/types.py`
- Modify: `agents/atlas.py` (SharedContext typing)

- [ ] **Step 1: Create agents/types.py with TypedDicts**

```python
"""Typed return values for agent execute() methods."""

from typing import TypedDict, NotRequired


class SageTriageResult(TypedDict):
    agent: str
    status: str
    issues: list[dict]
    total_analyzed: int
    prompt_used: NotRequired[str]


class EchoSocialResult(TypedDict):
    agent: str
    status: str
    top_mentions: list[dict]
    total_mentions: int
    prompt_used: NotRequired[str]


class IrisThemesResult(TypedDict):
    agent: str
    status: str
    themes: list[dict]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class NovaExperimentResult(TypedDict):
    agent: str
    status: str
    experiments: list[dict]
    prompt_used: NotRequired[str]


class KaiContentResult(TypedDict):
    agent: str
    status: str
    content_type: NotRequired[str]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class RexCompetitiveResult(TypedDict):
    agent: str
    status: str
    competitors_discovered: list[str]
    kb_sources: list[str]
    web_intel_sources: dict[str, int]
    upstream_social_mentions: NotRequired[int]
    upstream_community_issues: NotRequired[int]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class PaxSalesResult(TypedDict):
    agent: str
    status: str
    asset_type: str
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class MoxCampaignResult(TypedDict):
    agent: str
    status: str
    asset_type: str
    prompt_used: NotRequired[str]
    content: NotRequired[dict]
```

- [ ] **Step 2: Run type check (if mypy is available)**

Run: `python3 -m mypy agents/types.py --ignore-missing-imports 2>&1 | tail -5`

- [ ] **Step 3: Commit**

```bash
git add agents/types.py
git commit -m "feat: add TypedDict return types for all agents"
```

---

## Chunk 4: Per-agent Config + Structured Logging

### Task 15: Add per-agent config support

**Files:**
- Modify: `agents/config.py`
- Modify: `config/agent_config.yaml`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test for per-agent config**

```python
# tests/test_config.py
"""Tests for agent configuration."""

import pytest
from pathlib import Path
from agents.config import AgentConfig, load_config


class TestAgentConfig:
    def test_default_config(self):
        config = AgentConfig()
        assert config.retry_settings["max_retries"] == 3

    def test_get_agent_config_returns_defaults(self):
        config = AgentConfig()
        agent_cfg = config.get_agent_config("sage")
        assert agent_cfg["temperature"] == 0.7
        assert agent_cfg["model"] == "claude-sonnet-4-5-20250929"

    def test_get_agent_config_with_override(self):
        config = AgentConfig(
            agents={"kai": {"temperature": 0.9, "max_tokens": 8192}}
        )
        kai_cfg = config.get_agent_config("kai")
        assert kai_cfg["temperature"] == 0.9
        assert kai_cfg["max_tokens"] == 8192

    def test_load_config_from_yaml(self, tmp_path):
        yaml_content = """
agents:
  kai:
    temperature: 0.9
    max_tokens: 8192
  sage:
    temperature: 0.3
retry_settings:
  max_retries: 5
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_file)
        assert config.get_agent_config("kai")["temperature"] == 0.9
        assert config.get_agent_config("sage")["temperature"] == 0.3
        assert config.retry_settings["max_retries"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL (`get_agent_config` doesn't exist)

- [ ] **Step 3: Add get_agent_config method to AgentConfig**

```python
# In agents/config.py, add to AgentConfig class:

DEFAULT_AGENT_CONFIG = {
    "temperature": 0.7,
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 4096,
}

# Add method to AgentConfig:
def get_agent_config(self, agent_name: str) -> dict[str, Any]:
    """Get config for a specific agent, with defaults."""
    defaults = dict(DEFAULT_AGENT_CONFIG)
    overrides = self.agents.get(agent_name, {})
    return {**defaults, **overrides}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agents/config.py tests/test_config.py
git commit -m "feat: add per-agent config with get_agent_config method"
```

---

### Task 16: Add token cost tracking to LLMClient

**Files:**
- Modify: `agents/llm.py`
- Create: `tests/test_llm_cost_tracking.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_llm_cost_tracking.py
"""Tests for LLM client cost tracking."""

from agents.llm import LLMClient, TokenUsage


class TestTokenUsage:
    def test_token_usage_init(self):
        usage = TokenUsage()
        assert usage.total_input_tokens == 0
        assert usage.total_output_tokens == 0
        assert usage.total_calls == 0

    def test_token_usage_record(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        assert usage.total_input_tokens == 100
        assert usage.total_output_tokens == 50
        assert usage.total_calls == 1

    def test_token_usage_accumulates(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        usage.record(input_tokens=200, output_tokens=100)
        assert usage.total_input_tokens == 300
        assert usage.total_output_tokens == 150
        assert usage.total_calls == 2

    def test_token_usage_to_dict(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        d = usage.to_dict()
        assert d["total_input_tokens"] == 100
        assert d["total_output_tokens"] == 50
        assert d["total_calls"] == 1


class TestLLMClientHasUsage:
    def test_client_has_usage_tracker(self):
        client = LLMClient(api_key="test")
        assert hasattr(client, "usage")
        assert isinstance(client.usage, TokenUsage)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_llm_cost_tracking.py -v`
Expected: FAIL

- [ ] **Step 3: Add TokenUsage class and tracking to LLMClient**

```python
# In agents/llm.py, add:

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    """Tracks cumulative token usage across LLM calls."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_calls": self.total_calls,
        }


# In LLMClient.__init__, add:
self.usage = TokenUsage()

# In LLMClient.generate(), after the API call, add:
self.usage.record(
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_llm_cost_tracking.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agents/llm.py tests/test_llm_cost_tracking.py
git commit -m "feat: add TokenUsage tracking to LLMClient"
```

---

### Task 17: Add structured logging for agent execution

**Files:**
- Modify: `agents/atlas.py`

- [ ] **Step 1: Add structured log output to delegate() and run_weekly_cycle()**

In `delegate()`, after a successful or failed delegation, log structured data:

```python
logger.info(
    "delegation_complete",
    extra={
        "agent": agent_name,
        "task": task[:80],
        "success": result.success,
        "attempts": attempt,
        "error": last_error if not result.success else None,
    },
)
```

In `run_weekly_cycle()`, at the end log a summary:

```python
logger.info(
    "weekly_cycle_complete",
    extra={
        "week": self.context.week_of,
        "okr_progress": self.context.okr_progress,
        "llm_usage": self.llm_client.usage.to_dict() if self.llm_client else None,
    },
)
```

- [ ] **Step 2: Run full test suite to verify no regressions**

Run: `python3 -m pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: 0 failures

- [ ] **Step 3: Commit**

```bash
git add agents/atlas.py
git commit -m "feat: add structured logging to Atlas delegate and weekly cycle"
```

---

## Chunk 5: Edge Case Tests + Final Verification

### Task 18: Add test fixtures directory

**Files:**
- Create: `tests/fixtures/sample_llm_responses.json`

- [ ] **Step 1: Create fixture file with edge case LLM responses**

```json
{
  "valid_json_response": "{\"themes\": [{\"theme_id\": \"t1\", \"title\": \"Test\"}]}",
  "json_with_markdown_fence": "```json\n{\"themes\": []}\n```",
  "empty_json_object": "{}",
  "malformed_json": "{not valid json",
  "empty_string": "",
  "very_long_response": "x repeat 10000 times",
  "json_with_extra_text": "Here is the analysis:\n```json\n{\"threats\": []}\n```\nHope this helps!"
}
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/sample_llm_responses.json
git commit -m "feat: add LLM response fixtures for edge case testing"
```

---

### Task 19: Add edge case tests for agent error handling

**Files:**
- Create: `tests/test_agent_edge_cases.py`

- [ ] **Step 1: Write edge case tests**

```python
"""Edge case tests for agent error handling."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from agents.rex import Rex
from agents.pax import Pax
from agents.mox import Mox
from agents.iris import Iris


class TestLLMResponseEdgeCases:
    """Test agents handle unusual LLM responses gracefully."""

    @pytest.mark.asyncio
    async def test_rex_handles_malformed_json(
        self, posthog_client, knowledge_base_path, mock_llm_client,
    ):
        mock_llm_client.generate = AsyncMock(return_value="{not valid json")
        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await rex.execute("Analyze for: TestCorp")
        # Should not crash — may return prompt_used or partial result
        assert result["agent"] == "rex"
        assert result["status"] == "generated"

    @pytest.mark.asyncio
    async def test_pax_handles_empty_llm_response(
        self, posthog_client, knowledge_base_path, mock_llm_client,
    ):
        mock_llm_client.generate = AsyncMock(return_value="")
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await pax.execute("Generate battle card for TestProduct")
        assert result["agent"] == "pax"

    @pytest.mark.asyncio
    async def test_mox_handles_json_with_fences(
        self, posthog_client, knowledge_base_path, mock_llm_client,
    ):
        mock_llm_client.generate = AsyncMock(
            return_value='```json\n{"title": "Test Post"}\n```'
        )
        mox = Mox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await mox.execute("Write blog post about feature X")
        assert result["agent"] == "mox"
        assert result["status"] == "generated"


class TestEmptyKnowledgeBase:
    """Test agents work with empty knowledge bases."""

    @pytest.mark.asyncio
    async def test_rex_empty_kb(self, posthog_client, tmp_path):
        empty_kb = tmp_path / "empty_kb"
        empty_kb.mkdir()
        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=empty_kb,
        )
        result = await rex.execute("Analyze for: TestCorp")
        assert result["agent"] == "rex"
        assert result["kb_sources"] == []

    @pytest.mark.asyncio
    async def test_pax_empty_kb(self, posthog_client, tmp_path):
        empty_kb = tmp_path / "empty_kb"
        empty_kb.mkdir()
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=empty_kb,
        )
        result = await pax.execute("Generate battle card")
        assert result["agent"] == "pax"

    @pytest.mark.asyncio
    async def test_mox_empty_kb(self, posthog_client, tmp_path):
        empty_kb = tmp_path / "empty_kb"
        empty_kb.mkdir()
        mox = Mox(
            api_client=posthog_client,
            knowledge_base_path=empty_kb,
        )
        result = await mox.execute("Write blog post")
        assert result["agent"] == "mox"
```

- [ ] **Step 2: Run edge case tests**

Run: `python3 -m pytest tests/test_agent_edge_cases.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_edge_cases.py
git commit -m "feat: add edge case tests for agent error handling"
```

---

### Task 20: Final full test suite verification

- [ ] **Step 1: Run entire test suite**

Run: `python3 -m pytest tests/ -v --tb=short 2>&1 | tail -10`
Expected: 0 failures, ~420+ passed

- [ ] **Step 2: Run ruff check for linting**

Run: `ruff check agents/ tests/ --fix`

- [ ] **Step 3: Final commit if any lint fixes**

```bash
git add -A
git commit -m "chore: lint fixes"
```
