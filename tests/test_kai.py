"""Tests for Kai content creator module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devrel_origin.core.kai import ContentPiece, Kai
from devrel_origin.tools.search_tools import SearchTools


@pytest.fixture
def kai(posthog_client, knowledge_base_path):
    return Kai(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestKaiKnowledgeBase:
    """Test knowledge base search."""

    def test_search_finds_matching_docs(self, kai):
        results = kai.search_knowledge_base("python sdk")
        assert len(results) >= 1
        assert "python" in results[0]["source"].lower()

    def test_search_no_results(self, kai):
        results = kai.search_knowledge_base("nonexistent topic xyz")
        # No keyword matches, but the fallback fills up to max_results
        # from remaining kb docs, so we get results with relevance=0
        assert all(r["relevance"] == 0 for r in results)


class TestKaiExecuteWired:
    """Test that execute() generates content via LLM."""

    @pytest.fixture
    def wired_kai(self, posthog_client, knowledge_base_path, mock_llm_client, monkeypatch):
        mock_llm_client.generate = AsyncMock(
            return_value=(
                "# Getting Started with PostHog Analytics\n\n"
                "This tutorial walks you through tracking events...\n\n"
                "## Prerequisites\n- PostHog account\n- JavaScript SDK installed\n\n"
                "## Step 1: Track an event\n```javascript\nposthog.capture('page_view')\n```\n"
            )
        )

        async def fake_pipeline(*, llm_client, system_prompt, user_prompt, content_type, logger):
            content = await llm_client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            return content, ["grounded example"], []

        monkeypatch.setattr("devrel_origin.core.kai.generate_with_pipeline", fake_pipeline)
        return Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_generates_content(self, wired_kai, mock_llm_client):
        result = await wired_kai.execute("Write a tutorial on analytics tracking")
        assert result["status"] == "generated"
        assert "Track an event" in result["content"]
        mock_llm_client.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_fast_mode_bypasses_editorial_pipeline(
        self, posthog_client, tmp_path, mock_llm_client
    ):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text("# Analytics tracking\nTrack analytics events.")
        (kb / "billing.md").write_text("# Billing\nInvoices and subscriptions.")
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=kb,
            llm_client=mock_llm_client,
        )
        mock_llm_client.generate = AsyncMock(return_value="# Draft\n\nTrack events.")

        async def broken_pipeline(**_):
            raise AssertionError("full editorial pipeline should not run in fast mode")

        with patch("devrel_origin.core.kai.generate_with_pipeline", new=broken_pipeline):
            result = await kai.execute("Write about analytics tracking", editorial_mode="fast")

        assert result["status"] == "generated"
        assert result["editorial_mode"] == "fast"
        assert result["revision"]["strengths"] == ["fast grounded draft"]

    @pytest.mark.asyncio
    async def test_execute_repairs_invalid_code_blocks(
        self, posthog_client, tmp_path, mock_llm_client
    ):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text("# Analytics tracking\nTeam path cleaning filters.")
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=kb,
            llm_client=mock_llm_client,
        )
        mock_llm_client.generate = AsyncMock(
            return_value="# Draft\n\n```python\nteam = Team.objects.get(id=<project_id>)\n```"
        )

        result = await kai.execute(
            "Write about analytics tracking",
            context={"dex_docs": {"architecture_doc": "Team path cleaning filters."}},
            editorial_mode="fast",
        )

        assert result["status"] == "generated"
        assert "Team.objects.get" not in result["content"]
        assert result["code_validation"]["all_passed"] is True
        assert result["code_validation"]["deterministic_repair"] is True

    @pytest.mark.asyncio
    async def test_execute_includes_grounding_sources(self, wired_kai):
        result = await wired_kai.execute("Write about analytics tracking")
        assert "grounding_sources" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_prompt(self, kai):
        result = await kai.execute("Write a tutorial about analytics tracking")
        assert result["status"] == "generated"
        assert "content" not in result
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_uses_upstream_themes(self, wired_kai):
        context = {
            "iris_themes": {
                "themes": [
                    {"title": "SDK init pain", "severity": 7.0},
                ],
            },
        }
        result = await wired_kai.execute("Write python sdk tutorial", context=context)
        assert len(result.get("pain_points_addressed", [])) >= 1

    @pytest.mark.asyncio
    async def test_execute_blocks_when_no_evidence(self, posthog_client, tmp_path):
        empty_kb = tmp_path / "kb"
        empty_kb.mkdir()
        kai = Kai(api_client=posthog_client, knowledge_base_path=empty_kb)
        result = await kai.execute("Write a tutorial about imaginary integrations")
        assert result["status"] == "insufficient_evidence"
        assert result["evidence_gaps"]

    @pytest.mark.asyncio
    async def test_negated_github_issue_wording_does_not_require_issues(self, kai):
        result = await kai.execute(
            "Write about analytics tracking. Avoid GitHub issue claims unless issue evidence is available."
        )
        assert result["status"] == "generated"
        assert not result.get("evidence_gaps")

    @pytest.mark.asyncio
    async def test_negated_issue_wording_does_not_pollute_kb_search(self, posthog_client, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text("# Analytics tracking\nTrack events and query insights.")
        (kb / "error-issues.md").write_text("# Error tracking issues\nQuery error tracking issues.")
        kai = Kai(api_client=posthog_client, knowledge_base_path=kb)

        result = await kai.execute(
            "Write about analytics tracking. Avoid GitHub issue claims unless issue evidence is available."
        )

        assert result["status"] == "generated"
        assert result["grounding_sources"][0] == "analytics.md"

    @pytest.mark.asyncio
    async def test_file_path_request_accepts_kb_path_evidence(self, posthog_client, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text(
            "# Analytics\nDebug analytics tracking in `posthog/hogql_queries/query_runner.py`."
        )
        kai = Kai(api_client=posthog_client, knowledge_base_path=kb)
        result = await kai.execute(
            "Write about analytics tracking and include concrete file paths."
        )
        assert result["status"] == "generated"
        assert not result.get("evidence_gaps")

    @pytest.mark.asyncio
    async def test_grounding_gate_rewrites_unsupported_mcp_calls(
        self, posthog_client, tmp_path, mock_llm_client
    ):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text(
            "# Analytics\nUse `POST /api/projects/@current/query/` for query execution."
        )
        (kb / "billing.md").write_text("# Billing\nInvoices and subscriptions.")
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=kb,
            llm_client=mock_llm_client,
        )

        async def fake_pipeline(**_):
            return (
                "```python\nposthog_mcp_call('posthog:query-trends', {})\n```",
                ["structure"],
                [],
            )

        mock_llm_client.generate = AsyncMock(
            return_value="Use `POST /api/projects/@current/query/` with a documented query payload."
        )

        with patch("devrel_origin.core.kai.generate_with_pipeline", new=fake_pipeline):
            result = await kai.execute("Write about analytics query freshness")

        assert result["status"] == "generated"
        assert "posthog_mcp_call" not in result["content"]
        assert result["grounding_validation"]["rewritten"] is True
        assert result["grounding_validation"]["all_passed"] is True

    @pytest.mark.asyncio
    async def test_grounding_gate_blocks_when_rewrite_still_unsupported(
        self, posthog_client, tmp_path, mock_llm_client
    ):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "analytics.md").write_text("# Analytics\nTrack events.")
        (kb / "billing.md").write_text("# Billing\nInvoices and subscriptions.")
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=kb,
            llm_client=mock_llm_client,
        )

        async def fake_pipeline(**_):
            return (
                "```python\nposthog_mcp_call('posthog:query-trends', {})\n```",
                ["structure"],
                [],
            )

        mock_llm_client.generate = AsyncMock(
            return_value="```python\nposthog_mcp_call('posthog:query-trends', {})\n```"
        )

        with patch("devrel_origin.core.kai.generate_with_pipeline", new=fake_pipeline):
            result = await kai.execute("Write about analytics query freshness")

        assert result["status"] == "blocked_by_grounding_gate"
        assert result["content"] == ""
        assert result["grounding_validation"]["all_passed"] is False

    def test_grounding_guard_flags_unverified_posthog_diagnostics(self, kai):
        evidence = (
            "[Source: analytics/lazy-computation-consistency.md]\n"
            "preaggregation_results, sharded_preaggregation_results, system.replicas"
        )
        content = """
**Source**: `products/analytics_platform/backend/lazy_computation/CONSISTENCY.md`

Check `docker/clickhouse/users.xml`, enable `HOGQL_QUERY_LOG`, and tail
`/var/log/posthog/hogql.log`.

```sql
SELECT id, status FROM lazy_computation_jobs;
```
"""

        issues = kai._grounded_output_issues(
            content,
            evidence,
            allowed_source_ids=["analytics/lazy-computation-consistency.md"],
        )
        issue_text = "\n".join(issue["issue"] for issue in issues)

        assert (
            "cites `products/analytics_platform/backend/lazy_computation/CONSISTENCY.md`"
            in issue_text
        )
        assert "unsupported file path `docker/clickhouse/users.xml`" in issue_text
        assert "unsupported setting or constant `HOGQL_QUERY_LOG`" in issue_text
        assert "unsupported file path `/var/log/posthog/hogql.log`" in issue_text
        assert "unsupported database table `lazy_computation_jobs`" in issue_text

    def test_grounding_guard_flags_internal_markers_and_unsupported_sql_columns(self, kai):
        evidence = "[Source: analytics.md]\n`system.replicas` has `queue_size`."
        content = """
Use this direct ClickHouse query (evidence truncated):

```sql
SELECT total_replicas, active_replicas, queue_size
FROM system.replicas;
```
"""
        issues = kai._grounded_output_issues(
            content,
            evidence,
            allowed_source_ids=["analytics.md"],
            task="Write a web analytics freshness diagnostic",
        )
        issue_text = "\n".join(issue["issue"] for issue in issues)

        assert "internal context-truncation marker" in issue_text
        assert "unsupported identifier or column `active_replicas`" in issue_text
        assert "unsupported identifier or column `total_replicas`" in issue_text

    def test_grounding_guard_flags_prose_mcp_tool_references(self, kai):
        issues = kai._grounded_output_issues(
            "Use the MCP `project-settings-update` tool to modify path cleaning rules.",
            "Path cleaning rules live in Team.path_cleaning_filters.",
            allowed_source_ids=["web-analytics/managing-path-cleaning-rules.md"],
        )
        assert any(
            "unsupported MCP tool `project-settings-update`" in issue["issue"] for issue in issues
        )

    def test_grounding_guard_does_not_treat_python_import_as_sql_table(self, kai):
        issues = kai._grounded_output_issues(
            "```python\nfrom posthog.models import Team\n```",
            "Team path cleaning filters are stored on teams.",
            allowed_source_ids=["web-analytics/managing-path-cleaning-rules.md"],
        )
        issue_text = "\n".join(issue["issue"] for issue in issues)

        assert "unsupported internal module `posthog.models`" in issue_text
        assert "unsupported database table `posthog.models`" not in issue_text

    def test_sanitize_internal_markers_removes_context_leakage(self, kai):
        assert (
            kai._sanitize_internal_markers("Uses system tables (evidence truncated).")
            == "Uses system tables."
        )

    def test_normalize_unsupported_placeholders_rewrites_config_like_values(self, kai):
        assert (
            kai._normalize_unsupported_placeholders("Bearer YOUR_TOKEN and YOUR_API_KEY")
            == "Bearer <your-token> and <your-api-key>"
        )

    def test_remove_unsupported_sql_blocks_replaces_unevidenced_columns(self, kai):
        evidence = "`system.replicas` has `queue_size`."
        content = """
Run:

```sql
SELECT total_replicas, active_replicas, queue_size
FROM system.replicas;
```
"""
        repaired = kai._remove_unsupported_sql_blocks(content, evidence)

        assert "```sql" not in repaired
        assert "total_replicas" in repaired
        assert "source material does not verify" in repaired
        assert "Inspect the referenced" not in repaired

    def test_remove_dead_end_lines_rewrites_required_source_inspection(self, kai):
        content = """
## Check
The evidence does not specify the schema, so inspect `posthog/example.py`.
The evidence shows job filters but does not provide exact columns; consult `posthog/README.md` before running.
The evidence does not specify the Postgres schema. It lives in `posthog/jobs.py`.
The evidence does not specify the signature for the `project-settings-update` MCP tool.
If dispatch fails, inspect `query_runner.py` for missing imports.
Continue with verified checks.
"""
        repaired = kai._remove_dead_end_lines(content)

        assert "inspect `posthog/example.py`" not in repaired
        assert "consult `posthog/README.md`" not in repaired
        assert "`posthog/jobs.py`" not in repaired
        assert "MCP tool" not in repaired
        assert "inspect `query_runner.py`" not in repaired
        assert "Evidence limitation" in repaired
        assert "Continue with verified checks." in repaired

    def test_remove_unsupported_internal_import_blocks_replaces_runnable_imports(self, kai):
        content = """
Use this example:

```python
from posthog.models import Team
print(Team)
```
"""
        repaired = kai._remove_unsupported_internal_imports(content, "Team path cleaning filters.")

        assert "from posthog.models import Team" not in repaired
        assert "Evidence limitation" in repaired
        assert "posthog.models" in repaired

    def test_remove_invalid_code_blocks_replaces_failed_snippets(self, kai):
        from devrel_origin.tools.code_validator import CodeValidator

        content = """
```python
team = Team.objects.get(id=<project_id>)
```
"""
        report = CodeValidator().validate_content(content)
        repaired = kai._remove_invalid_code_blocks(content, report.errors)

        assert "Team.objects.get" not in repaired
        assert "failed syntax validation" in repaired

    def test_demote_limitation_only_checks_removes_fake_action_steps(self, kai):
        content = """
### Check 4: Verify Job Coverage

> Evidence limitation: the current KB evidence does not verify a self-contained command.

### Check 5: Empty

## Next
Continue.
"""
        repaired = kai._demote_limitation_only_checks(content)

        assert "### Check 4" not in repaired
        assert "### Check 5" not in repaired
        assert "### Evidence limitation: Verify Job Coverage" in repaired
        assert "## Next" in repaired

    def test_grounding_guard_flags_missing_web_analytics_diagnostic_section(self, kai):
        evidence = "[Source: web-analytics/live.md]\nWeb analytics live traffic checks."
        content = """
# Web Analytics Freshness

## Architecture Context
Web analytics uses query runners.
"""
        issues = kai._grounded_output_issues(
            content,
            evidence,
            allowed_source_ids=["web-analytics/live.md"],
            task="Write web analytics freshness diagnostics",
        )
        assert any(
            "dedicated diagnostic web analytics section" in issue["issue"] for issue in issues
        )

    def test_grounding_guard_flags_unused_web_analytics_evidence_and_dead_ends(self, kai):
        evidence = (
            "[Source: web-analytics/managing-path-cleaning-rules.md]\n"
            "Path cleaning rules normalize URLs.\n"
            "[Source: web-analytics/exploring-live-traffic.md]\n"
            "Live traffic shows recent matching events."
        )
        content = """
# Web Analytics Freshness

## Web Analytics Freshness Diagnostics
The evidence does not specify the table schema, so inspect `posthog/example.py`.
"""
        issues = kai._grounded_output_issues(
            content,
            evidence,
            allowed_source_ids=[
                "web-analytics/managing-path-cleaning-rules.md",
                "web-analytics/exploring-live-traffic.md",
            ],
            task="Write web analytics freshness diagnostics",
        )
        issue_text = "\n".join(issue["issue"] for issue in issues)

        assert "path-cleaning evidence is available" in issue_text
        assert "live-traffic evidence is available" in issue_text
        assert "dead-end" in issue_text

    def test_grounding_guard_allows_evidenced_repo_paths_when_contextual(self, kai):
        evidence = (
            "[Source: repo/query-api-and-web-analytics-source-evidence.md]\n"
            "`posthog/hogql_queries/query_runner.py` contains get_query_runner."
        )
        content = (
            "The query runner lives in `posthog/hogql_queries/query_runner.py`.\n\n"
            "## Sources\n"
            "- `repo/query-api-and-web-analytics-source-evidence.md`\n"
        )

        issues = kai._grounded_output_issues(
            content,
            evidence,
            allowed_source_ids=["repo/query-api-and-web-analytics-source-evidence.md"],
        )

        assert issues == []


class TestKaiOfficialDocsValidation:
    """Test that Kai consults official docs when search_tools is provided."""

    @pytest.mark.asyncio
    async def test_execute_fetches_official_docs(self, posthog_client, knowledge_base_path):
        mock_search = MagicMock(spec=SearchTools)
        mock_search.fetch_official_docs = AsyncMock(
            return_value="## Feature Flags\nOfficial docs on feature flags."
        )
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
        )
        result = await kai.execute("Write about feature flags")
        mock_search.fetch_official_docs.assert_awaited_once()
        # The prompt should contain the official docs
        assert "prompt_used" in result
        assert "Official Documentation Reference" in result["prompt_used"]

    @pytest.mark.asyncio
    async def test_execute_without_search_tools_still_works(self, kai):
        result = await kai.execute("Write about analytics tracking")
        assert result["status"] == "generated"
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_handles_docs_fetch_failure(self, posthog_client, knowledge_base_path):
        mock_search = MagicMock(spec=SearchTools)
        mock_search.fetch_official_docs = AsyncMock(side_effect=Exception("Network error"))
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
        )
        result = await kai.execute("Write about analytics tracking")
        # Should not crash — degrades gracefully
        assert result["status"] == "generated"


class TestKaiWriteTutorial:
    """Test write_tutorial() convenience method."""

    @pytest.mark.asyncio
    async def test_write_tutorial_returns_content_piece(self, kai):
        result = await kai.write_tutorial("Setting up PostHog")
        assert isinstance(result, ContentPiece)
        assert result.content_type == "tutorial"


class TestKaiContentTypeRouting:
    """Test that content_type flows through to the editorial pipeline."""

    @pytest.mark.asyncio
    async def test_write_changelog_uses_landing_page_content_type(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        mock_llm_client.generate = AsyncMock(return_value="# Changelog body")
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        captured: dict = {}

        async def fake_pipeline(*, content_type, **_):
            captured["content_type"] = content_type
            return ("body", ["s"], [])

        with patch("devrel_origin.core.kai.generate_with_pipeline", new=fake_pipeline):
            await kai.write_changelog("New SDK")
        assert captured["content_type"] == "landing_page"

    @pytest.mark.asyncio
    async def test_pipeline_string_issues_preserved_in_remaining_issues(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

        async def fake_pipeline(**_):
            # Simulate pipeline returning string issues (the editorial pipeline path)
            return (
                "body",
                ["good thing"],
                ["readability concern", "voice mismatch", "  "],
            )

        with patch("devrel_origin.core.kai.generate_with_pipeline", new=fake_pipeline):
            result = await kai.execute("Write about analytics tracking")

        # String issues should now survive the filter; the empty/whitespace one is dropped
        remaining = result["revision"]["remaining_issues"]
        assert "readability concern" in remaining
        assert "voice mismatch" in remaining
        assert "  " not in remaining
