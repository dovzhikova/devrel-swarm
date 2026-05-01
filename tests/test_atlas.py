"""Tests for Atlas orchestrator module."""

import json
from unittest.mock import AsyncMock

import pytest

from devrel_swarm.core.atlas import Atlas, DelegationResult, SharedContext
from devrel_swarm.core.mox import Mox
from devrel_swarm.core.pax import Pax
from devrel_swarm.core.rex import Rex


class TestSharedContext:
    """Test SharedContext creation and serialization."""

    def test_create_shared_context(self):
        context = SharedContext(week_of="2026-W11")
        assert context.week_of == "2026-W11"
        assert context.sage_triage == {}
        assert context.iris_themes == {}

    def test_shared_context_serialization(self):
        context = SharedContext(
            week_of="2026-W11",
            sage_triage={"issues": [{"id": "issue_1"}]},
            iris_themes={"themes": ["sdk_friction"]},
        )
        data = context.to_dict()
        assert data["week_of"] == "2026-W11"
        assert len(data["sage_triage"]["issues"]) == 1
        assert "sdk_friction" in data["iris_themes"]["themes"]

    def test_shared_context_save(self, tmp_path):
        context = SharedContext(week_of="2026-W11")
        context.save(tmp_path / "archive")
        assert (tmp_path / "archive" / "context_2026-W11.json").exists()


class TestDelegationResult:
    """Test DelegationResult tracking."""

    def test_delegation_result_success(self):
        result = DelegationResult(
            agent="sage",
            task="analyze_issues",
            success=True,
            output={"issues_analyzed": 5},
        )
        assert result.agent == "sage"
        assert result.success is True
        assert result.output["issues_analyzed"] == 5

    def test_delegation_result_failure(self):
        result = DelegationResult(
            agent="nova",
            task="calculate_metrics",
            success=False,
            error="API timeout",
        )
        assert result.success is False
        assert result.error == "API timeout"


class TestAtlasRetryLogic:
    """Test Atlas delegate() retry logic."""

    @pytest.mark.asyncio
    async def test_delegate_success(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        result = await atlas.delegate("sage", "triage issues")
        assert result.success is True
        assert result.agent == "sage"

    @pytest.mark.asyncio
    async def test_delegate_unknown_agent(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        result = await atlas.delegate("nonexistent", "do something")
        assert result.success is False
        assert "Unknown agent" in result.error

    @pytest.mark.asyncio
    async def test_delegate_retries_on_failure(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        call_count = 0

        async def failing_execute(task, context=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Transient error")
            return {"status": "ok"}

        atlas.sage.execute = failing_execute
        atlas.BASE_DELAY = 0.01  # speed up test

        result = await atlas.delegate("sage", "triage issues")
        assert result.success is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_delegate_exhausted_retries(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )

        async def always_fail(task, context=None):
            raise RuntimeError("Persistent error")

        atlas.sage.execute = always_fail
        atlas.BASE_DELAY = 0.01

        result = await atlas.delegate("sage", "triage issues")
        assert result.success is False
        assert "Persistent error" in result.error


class TestAtlasOrchestration:
    """Test Atlas orchestration workflow."""

    @pytest.mark.asyncio
    async def test_run_weekly_cycle(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=knowledge_base_path.parent / "archive",
        )
        context = await atlas.run_weekly_cycle()
        assert isinstance(context, SharedContext)
        assert context.okr_progress["status"] == "complete"

    @pytest.mark.asyncio
    async def test_weekly_cycle_agent_order(self, posthog_client, knowledge_base_path):
        """Verify Sage runs before Nova (upstream dependency)."""
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=knowledge_base_path.parent / "archive",
        )
        call_order = []
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name, task, context=None):
            call_order.append(agent_name)
            return await original_delegate(agent_name, task, context)

        atlas.delegate = tracking_delegate
        await atlas.run_weekly_cycle()

        assert "sage" in call_order
        assert "nova" in call_order
        assert call_order.index("sage") < call_order.index("nova")


class TestAtlasWithDependencies:
    """Test Atlas passes LLM and GitHub tools to agents."""

    @pytest.mark.asyncio
    async def test_atlas_with_full_dependencies(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            github_tools=mock_github_tools,
        )
        assert atlas.sage.github_tools is mock_github_tools
        assert atlas.iris.llm_client is mock_llm_client
        assert atlas.kai.llm_client is mock_llm_client

    @pytest.mark.asyncio
    async def test_weekly_cycle_with_dependencies(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
    ):
        import json

        mock_llm_client.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "themes": [
                        {
                            "theme_id": "t1",
                            "title": "SDK crashes",
                            "description": "SDK init fails",
                            "frequency": 2,
                            "severity": 7.0,
                            "sources": ["github"],
                            "representative_quotes": ["crash"],
                            "product_areas": ["sdks"],
                            "recommended_actions": ["Fix init"],
                            "journey_stage": "onboarding",
                        }
                    ]
                }
            )
        )
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            github_tools=mock_github_tools,
            archive_dir=tmp_path / "archive",
        )
        context = await atlas.run_weekly_cycle()
        # Sage should have triaged issues
        assert len(context.sage_triage.get("issues", [])) == 3
        # OKRs should reflect real data
        assert context.okr_progress["issues_triaged"] == 3


class TestAtlasSalesAgentRegistration:
    """Test that Rex, Pax, Mox are registered in Atlas."""

    def test_rex_registered(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert "rex" in atlas._agents
        assert isinstance(atlas._agents["rex"], Rex)

    def test_pax_registered(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert "pax" in atlas._agents
        assert isinstance(atlas._agents["pax"], Pax)

    def test_mox_registered(self, posthog_client, knowledge_base_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert "mox" in atlas._agents
        assert isinstance(atlas._agents["mox"], Mox)


class TestSharedContextSalesFields:
    """Test SharedContext includes new sales/marketing fields."""

    def test_rex_competitive_field_exists(self):
        ctx = SharedContext()
        assert hasattr(ctx, "rex_competitive")
        assert ctx.rex_competitive == {}

    def test_pax_sales_field_exists(self):
        ctx = SharedContext()
        assert hasattr(ctx, "pax_sales")
        assert ctx.pax_sales == {}

    def test_mox_campaigns_field_exists(self):
        ctx = SharedContext()
        assert hasattr(ctx, "mox_campaigns")
        assert ctx.mox_campaigns == {}

    def test_to_dict_includes_new_fields(self):
        ctx = SharedContext()
        d = ctx.to_dict()
        assert "rex_competitive" in d
        assert "pax_sales" in d
        assert "mox_campaigns" in d


class TestAtlasCheckpointResume:
    """Test that per-agent checkpoint flags allow partial-stage resume."""

    @pytest.mark.asyncio
    async def test_partial_stage_1_failure_only_reruns_failed_agent(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        """If Sage and Dex succeeded but Echo failed, resume re-runs only Echo."""
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Manually craft a stage-1 checkpoint where sage+dex completed but echo did not.
        week_of = "2026-W11"
        ctx = SharedContext(
            week_of=week_of,
            sage_triage={"issues": []},
            dex_docs={"modules": []},
        )
        d = ctx.to_dict()
        d.pop("previous_weeks", None)
        d["_checkpoint_stage"] = 1
        d["_completed_agents"] = ["dex", "sage", "watchdog"]
        (archive_dir / f"context_{week_of}_stage1.json").write_text(
            json.dumps(d, default=str)
        )

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=archive_dir,
        )
        atlas.context.week_of = week_of

        called: list[str] = []
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name, task, context=None):
            called.append(agent_name)
            return await original_delegate(agent_name, task, context)

        atlas.delegate = tracking_delegate
        await atlas.run_weekly_cycle()

        # Sage and Dex should NOT be re-invoked; Echo SHOULD be invoked exactly once
        assert called.count("sage") == 0
        assert called.count("dex") == 0
        assert called.count("echo") == 1
        # Watchdog also already completed, so should not be re-invoked
        assert called.count("watchdog") == 0


class TestAtlasWeeklyCycleRex:
    """Test Rex is called in the weekly cycle."""

    @pytest.mark.asyncio
    async def test_weekly_cycle_calls_rex(self, posthog_client, knowledge_base_path, tmp_path):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        call_order = []
        original_delegate = atlas.delegate

        async def tracking_delegate(agent_name, task, context=None):
            call_order.append(agent_name)
            return await original_delegate(agent_name, task, context)

        atlas.delegate = tracking_delegate
        await atlas.run_weekly_cycle()

        assert "rex" in call_order
        # Rex should run after sage/echo but before iris
        assert call_order.index("rex") < call_order.index("iris")
