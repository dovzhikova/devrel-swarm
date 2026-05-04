"""Edge case tests for agent utilities and shared components."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from devrel_swarm.core.base import KnowledgeBaseSearch, strip_markdown_fences

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_responses():
    """Load sample LLM responses from fixtures."""
    with open(FIXTURES_DIR / "sample_llm_responses.json") as f:
        return json.load(f)["test_cases"]


@pytest.fixture
def empty_kb(tmp_path):
    """Knowledge base with no files."""
    kb_dir = tmp_path / "empty_kb"
    kb_dir.mkdir()
    return KnowledgeBaseSearch(kb_dir)


@pytest.fixture
def populated_kb(tmp_path):
    """Knowledge base with various file types and content."""
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()

    # Create some markdown files
    (kb_dir / "getting-started.md").write_text(
        "# Getting Started\nInstall the SDK with pip install devrel-ai-agents.\n"
        "Configure your API key and you're ready to go."
    )
    (kb_dir / "feature-flags.md").write_text(
        "# Feature Flags\nFeature flags let you toggle features for specific users.\n"
        "Use the SDK to check flag values."
    )
    (kb_dir / "analytics.md").write_text(
        "# Analytics\nTrack events and analyze user behavior.\nUse capture() to send events."
    )
    sub = kb_dir / "advanced"
    sub.mkdir()
    (sub / "self-hosting.md").write_text(
        "# Self Hosting\nDeploy OpenClaw on your own infrastructure.\n"
        "Docker compose is the recommended approach."
    )

    return KnowledgeBaseSearch(kb_dir)


# ---------------------------------------------------------------------------
# strip_markdown_fences edge cases
# ---------------------------------------------------------------------------


class TestStripMarkdownFencesEdgeCases:
    """Edge cases for markdown fence stripping."""

    def test_all_fixture_cases_parse_as_json(self, sample_responses):
        """Every fixture case should produce valid JSON after stripping."""
        for case in sample_responses:
            stripped = strip_markdown_fences(case["input"])
            parsed = json.loads(stripped)
            assert parsed is not None, f"Failed to parse case: {case['id']}"

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""

    def test_plain_text_unchanged(self):
        text = "Just some plain text with no fences"
        assert strip_markdown_fences(text) == text

    def test_nested_fences_only_strips_outer(self):
        text = '```json\n{"code": "```inner```"}\n```'
        result = strip_markdown_fences(text)
        assert "```inner```" in result

    def test_only_opening_fence(self):
        text = '```json\n{"key": "value"}'
        result = strip_markdown_fences(text)
        assert result == '{"key": "value"}'

    def test_only_closing_fence(self):
        text = '{"key": "value"}\n```'
        result = strip_markdown_fences(text)
        assert result == '{"key": "value"}'

    def test_python_fence_tag(self):
        text = "```python\nprint('hello')\n```"
        result = strip_markdown_fences(text)
        assert result == "print('hello')"

    def test_whitespace_only(self):
        assert strip_markdown_fences("   ") == ""


# ---------------------------------------------------------------------------
# KnowledgeBaseSearch edge cases
# ---------------------------------------------------------------------------


class TestKnowledgeBaseSearchEdgeCases:
    """Edge cases for knowledge base search."""

    def test_empty_kb_returns_empty(self, empty_kb):
        results = empty_kb.search("anything")
        assert results == []

    def test_empty_query(self, populated_kb):
        results = populated_kb.search("")
        # Empty query has no keywords after stop word removal, returns empty or padded
        assert isinstance(results, list)

    def test_stop_words_only_query(self, populated_kb):
        results = populated_kb.search("the is a an")
        assert isinstance(results, list)

    def test_search_finds_relevant_docs(self, populated_kb):
        results = populated_kb.search("feature flags toggle")
        assert len(results) > 0
        sources = [r["source"] for r in results]
        assert any("feature" in s for s in sources)

    def test_search_respects_limit(self, populated_kb):
        results = populated_kb.search("SDK install", limit=1)
        assert len(results) <= 1

    def test_search_as_text_returns_string(self, populated_kb):
        text = populated_kb.search_as_text("analytics events")
        assert isinstance(text, str)

    def test_search_as_text_empty_kb(self, empty_kb):
        text = empty_kb.search_as_text("anything")
        assert text == ""

    def test_index_includes_subdirectories(self, populated_kb):
        # self-hosting.md is in advanced/ subdirectory
        assert any("self hosting" in key for key in populated_kb.index.keys())

    def test_nonexistent_kb_path(self, tmp_path):
        kb = KnowledgeBaseSearch(tmp_path / "nonexistent")
        assert kb.index == {}
        assert kb.search("anything") == []

    def test_custom_stop_words(self, tmp_path):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "test.md").write_text("custom content here")
        kb = KnowledgeBaseSearch(kb_dir, extra_stop_words=frozenset({"custom"}))
        assert "custom" in kb.stop_words


# ---------------------------------------------------------------------------
# Atlas delegation edge cases
# ---------------------------------------------------------------------------


class TestAtlasDelegationEdgeCases:
    """Edge cases for Atlas task delegation."""

    @pytest.mark.asyncio
    async def test_delegate_unknown_agent(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        from devrel_swarm.core.atlas import Atlas

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await atlas.delegate("nonexistent_agent", "do something")
        assert result.success is False
        assert "Unknown agent" in result.error

    @pytest.mark.asyncio
    async def test_delegate_empty_task(self, posthog_client, knowledge_base_path, mock_llm_client):
        from devrel_swarm.core.atlas import Atlas

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        # Should not crash with empty task
        result = await atlas.delegate("sage", "")
        assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_delegate_retries_on_failure(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        from devrel_swarm.core.atlas import Atlas

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        atlas.BASE_DELAY = 0.001  # speed up test

        # Make the agent's execute method fail
        atlas.sage.execute = AsyncMock(side_effect=RuntimeError("API down"))

        result = await atlas.delegate("sage", "triage issues")
        assert result.success is False
        assert result.attempts == atlas.MAX_RETRIES + 1
        assert "API down" in result.error


# ---------------------------------------------------------------------------
# SharedContext edge cases
# ---------------------------------------------------------------------------


class TestSharedContextEdgeCases:
    """Edge cases for SharedContext."""

    def test_context_with_none_values(self):
        from devrel_swarm.core.atlas import SharedContext

        context = SharedContext(week_of="2026-W12")
        d = context.to_dict()
        # All dict fields should be empty dicts, not None
        for key in ["sage_triage", "iris_themes", "nova_experiments", "kai_content"]:
            assert d[key] == {}

    def test_context_save_creates_directory(self, tmp_path):
        from devrel_swarm.core.atlas import SharedContext

        context = SharedContext(week_of="2026-W12")
        deep_path = tmp_path / "a" / "b" / "c"
        context.save(deep_path)
        assert (deep_path / "context_2026-W12.json").exists()

    def test_context_save_valid_json(self, tmp_path):
        from devrel_swarm.core.atlas import SharedContext

        context = SharedContext(
            week_of="2026-W12",
            sage_triage={"issues": [{"id": 1}]},
        )
        context.save(tmp_path)
        saved = json.loads((tmp_path / "context_2026-W12.json").read_text())
        assert saved["week_of"] == "2026-W12"
        assert len(saved["sage_triage"]["issues"]) == 1
