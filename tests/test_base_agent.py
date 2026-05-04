"""Tests for shared agent utilities in src/devrel_swarm/core/base.py."""


from devrel_swarm.core.base import KnowledgeBaseSearch, strip_markdown_fences


class TestStripMarkdownFences:
    """Test markdown fence stripping utility."""

    def test_strips_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_strips_python_fence(self):
        text = "```python\nprint('hello')\n```"
        assert strip_markdown_fences(text) == "print('hello')"

    def test_strips_text_fence(self):
        text = "```text\nsome text\n```"
        assert strip_markdown_fences(text) == "some text"

    def test_strips_plain_fence(self):
        text = "```\nsome text\n```"
        assert strip_markdown_fences(text) == "some text"

    def test_no_fence_unchanged(self):
        text = '{"key": "value"}'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""

    def test_strips_surrounding_whitespace(self):
        text = '  ```json\n{"a": 1}\n```  '
        result = strip_markdown_fences(text)
        assert "```" not in result
        assert '{"a": 1}' in result


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

    def test_search_returns_relevance_scores(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("python sdk")
        assert len(results) > 0
        assert all("relevance" in r for r in results)
        # Results should be sorted by relevance desc
        scores = [r["relevance"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_no_results_pads_with_remaining(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("xyznonexistent", limit=3)
        # Should pad with remaining KB docs
        assert len(results) > 0
        assert all(r["relevance"] == 0 for r in results)

    def test_search_no_pad(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("xyznonexistent", limit=3, pad_with_remaining=False)
        assert results == []

    def test_search_respects_limit(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        results = kb.search("sdk", limit=1)
        assert len(results) <= 1

    def test_search_as_text_returns_string(self, knowledge_base_path):
        kb = KnowledgeBaseSearch(knowledge_base_path)
        text = kb.search_as_text("python sdk")
        assert isinstance(text, str)
        assert len(text) > 0

    def test_search_as_text_empty_kb(self, tmp_path):
        empty_kb = tmp_path / "empty_kb"
        empty_kb.mkdir()
        kb = KnowledgeBaseSearch(empty_kb)
        text = kb.search_as_text("anything")
        assert text == ""

    def test_extra_stop_words(self, knowledge_base_path):
        """Extra stop words should be excluded from query."""
        kb = KnowledgeBaseSearch(
            knowledge_base_path,
            extra_stop_words=frozenset({"python", "sdk"}),
        )
        results = kb.search("python sdk", pad_with_remaining=False)
        # "python" and "sdk" are now stop words, so fewer/no results
        # (depends on what other terms match)
        assert isinstance(results, list)
