"""Integration tests for the full Atlas weekly cycle.

Verifies end-to-end data flow through all 4 agents (Sage → Iris → Nova → Kai)
and that SharedContext is populated correctly at each stage.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from devrel_swarm.core.atlas import Atlas, SharedContext
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.github_tools import GitHubTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IRIS_THEMES_JSON = json.dumps(
    {
        "themes": [
            {
                "theme_id": "t1",
                "title": "SDK initialization failures",
                "description": "SDK init fails on React Native",
                "frequency": 2,
                "severity": 7.0,
                "sources": ["github"],
                "representative_quotes": ["Getting crash on startup"],
                "product_areas": ["sdks"],
                "recommended_actions": ["Fix React Native init"],
                "journey_stage": "onboarding",
            }
        ]
    }
)


def make_atlas(
    posthog_client: PostHogClient,
    knowledge_base_path: Path,
    mock_llm_client: LLMClient,
    mock_github_tools: GitHubTools,
    tmp_path: Path,
) -> Atlas:
    """Create a fully-wired Atlas instance with all mocks injected."""
    mock_llm_client.generate = AsyncMock(return_value=IRIS_THEMES_JSON)
    atlas = Atlas(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        github_tools=mock_github_tools,
        archive_dir=tmp_path / "archive",
    )
    atlas.BASE_DELAY = 0.01  # speed up retry tests
    return atlas


# ---------------------------------------------------------------------------
# Full weekly cycle integration tests
# ---------------------------------------------------------------------------


class TestWeeklyCycleIntegration:
    """Verify end-to-end data flow through all 4 agents."""

    @pytest.mark.asyncio
    async def test_full_cycle_completes(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """run_weekly_cycle returns a SharedContext with all fields populated."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )
        context = await atlas.run_weekly_cycle()

        assert isinstance(context, SharedContext)
        assert context.week_of != ""
        # All four agent output slots should be populated
        assert context.sage_triage != {}
        assert context.iris_themes != {}
        assert context.nova_experiments != {}
        assert context.kai_content != {}
        # OKR compilation is the final step
        assert context.okr_progress.get("status") == "complete"

    @pytest.mark.asyncio
    async def test_sage_populates_triage(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """After the cycle, sage_triage contains the 3 issues returned by the mock."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )
        context = await atlas.run_weekly_cycle()

        assert "issues" in context.sage_triage
        assert len(context.sage_triage["issues"]) == 3

    @pytest.mark.asyncio
    async def test_iris_receives_sage_data(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """Verify Iris's execute received sage_triage in the merged context."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        iris_received_context: dict = {}
        original_iris_execute = atlas.iris.execute

        async def capturing_iris_execute(task, context=None):
            nonlocal iris_received_context
            iris_received_context = context or {}
            return await original_iris_execute(task=task, context=context)

        atlas.iris.execute = capturing_iris_execute
        await atlas.run_weekly_cycle()

        # Iris must see sage_triage in its context
        assert "sage_triage" in iris_received_context
        sage_triage = iris_received_context["sage_triage"]
        assert "issues" in sage_triage
        assert len(sage_triage["issues"]) == 3

    @pytest.mark.asyncio
    async def test_nova_receives_iris_data(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """Verify Nova's execute received iris_themes in the merged context."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        nova_received_context: dict = {}
        original_nova_execute = atlas.nova.execute

        async def capturing_nova_execute(task, context=None):
            nonlocal nova_received_context
            nova_received_context = context or {}
            return await original_nova_execute(task=task, context=context)

        atlas.nova.execute = capturing_nova_execute
        await atlas.run_weekly_cycle()

        assert "iris_themes" in nova_received_context
        iris_themes = nova_received_context["iris_themes"]
        assert "themes" in iris_themes
        assert len(iris_themes["themes"]) >= 1

    @pytest.mark.asyncio
    async def test_kai_receives_all_upstream(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """Verify Kai's execute received all upstream data from sage, iris, and nova."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        kai_received_context: dict = {}
        original_kai_execute = atlas.kai.execute

        async def capturing_kai_execute(task, context=None):
            nonlocal kai_received_context
            kai_received_context = context or {}
            return await original_kai_execute(task=task, context=context)

        atlas.kai.execute = capturing_kai_execute
        await atlas.run_weekly_cycle()

        # Kai should see outputs from all three upstream agents
        assert "sage_triage" in kai_received_context
        assert "iris_themes" in kai_received_context
        assert "nova_experiments" in kai_received_context

    @pytest.mark.asyncio
    async def test_okr_progress_reflects_results(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """OKR progress should accurately count issues, themes, and experiments."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )
        context = await atlas.run_weekly_cycle()

        okr = context.okr_progress
        assert okr["status"] == "complete"
        # Sage returned 3 issues via the mock
        assert okr["issues_triaged"] == 3
        # Iris extracted at least 1 theme from the JSON mock
        assert okr["themes_identified"] >= 1
        # Nova designed experiments (only if iris produced themes)
        assert okr["experiments_designed"] >= 0
        # Kai produced content
        assert okr["content_produced"] is True

    @pytest.mark.asyncio
    async def test_context_archived_to_disk(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """After run_weekly_cycle, a JSON context file must exist in archive_dir."""
        archive_dir = tmp_path / "archive"
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )
        context = await atlas.run_weekly_cycle()

        expected_file = archive_dir / f"context_{context.week_of}.json"
        assert expected_file.exists(), f"Expected archive file not found: {expected_file}"

        # File should be valid JSON containing the correct week
        data = json.loads(expected_file.read_text())
        assert data["week_of"] == context.week_of
        assert "sage_triage" in data
        assert "iris_themes" in data
        assert "nova_experiments" in data
        assert "kai_content" in data
        assert "okr_progress" in data

    @pytest.mark.asyncio
    async def test_agent_execution_order(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """Track delegate calls and verify the strict sage < iris < nova < kai order."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        call_order: list[str] = []
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name: str, task: str, context=None):
            call_order.append(agent_name)
            return await original_delegate(agent_name, task, context)

        atlas.delegate = tracking_delegate
        await atlas.run_weekly_cycle()

        assert call_order.index("sage") < call_order.index("iris")
        assert call_order.index("iris") < call_order.index("nova")
        assert call_order.index("nova") < call_order.index("kai")


# ---------------------------------------------------------------------------
# Error recovery tests
# ---------------------------------------------------------------------------


class TestWeeklyCycleErrorRecovery:
    """Verify that the weekly cycle degrades gracefully when one agent fails."""

    @pytest.mark.asyncio
    async def test_cycle_continues_on_agent_failure(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """If Iris fails, Nova and Kai should still run and the cycle should complete."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        # Make Iris always fail
        async def always_fail(task, context=None):
            raise RuntimeError("Iris intentionally broken for test")

        atlas.iris.execute = always_fail
        atlas.BASE_DELAY = 0.01  # keep retries fast

        agents_called: list[str] = []
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name: str, task: str, context=None):
            agents_called.append(agent_name)
            return await original_delegate(agent_name, task, context)

        atlas.delegate = tracking_delegate
        context = await atlas.run_weekly_cycle()

        # Cycle must complete and return a SharedContext
        assert isinstance(context, SharedContext)
        assert context.okr_progress.get("status") == "complete"

        # All four agents must have been called despite Iris failing
        assert "sage" in agents_called
        assert "iris" in agents_called
        assert "nova" in agents_called
        assert "kai" in agents_called

    @pytest.mark.asyncio
    async def test_failed_agent_output_not_in_context(
        self,
        posthog_client: PostHogClient,
        knowledge_base_path: Path,
        mock_llm_client: LLMClient,
        mock_github_tools: GitHubTools,
        tmp_path: Path,
    ):
        """If Sage fails completely, sage_triage must remain empty in SharedContext."""
        atlas = make_atlas(
            posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
        )

        # Make Sage always fail (even on retries)
        async def always_fail(task, context=None):
            raise RuntimeError("Sage intentionally broken for test")

        atlas.sage.execute = always_fail
        atlas.BASE_DELAY = 0.01  # keep retries fast

        context = await atlas.run_weekly_cycle()

        # sage_triage should stay empty because every attempt raised
        assert context.sage_triage == {}
        # OKR should reflect zero issues triaged
        assert context.okr_progress["issues_triaged"] == 0
        # Cycle should still complete
        assert context.okr_progress.get("status") == "complete"
