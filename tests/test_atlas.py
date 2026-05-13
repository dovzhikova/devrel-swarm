"""Tests for Atlas orchestrator module."""

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_origin.core.agent_config import AgentConfig
from devrel_origin.core.atlas import Atlas, DelegationResult, SharedContext
from devrel_origin.core.mox import Mox
from devrel_origin.core.pax import Pax
from devrel_origin.core.rex import Rex
from devrel_origin.project import state as project_state
from devrel_origin.project.paths import ProjectPaths


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

    @pytest.mark.asyncio
    async def test_delegate_skips_retry_on_timeout(self, posthog_client, knowledge_base_path):
        """TimeoutError must not retry: retries re-burn the same expensive tokens
        (each editorial-pipeline attempt costs ~$0.30+) without changing the outcome.
        """
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )

        call_count = 0

        async def timeout_execute(task, context=None):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        atlas.sage.execute = timeout_execute
        atlas.BASE_DELAY = 0.01

        result = await atlas.delegate("sage", "triage issues")
        assert result.success is False
        assert "timed out" in result.error
        assert result.attempts == 1
        assert call_count == 1  # no retry burn

    @pytest.mark.asyncio
    async def test_delegate_hard_timeout_returns_if_agent_ignores_cancellation(
        self, posthog_client, knowledge_base_path
    ):
        """A stubborn agent must not keep Atlas.delegate blocked forever."""
        config = AgentConfig(agent_timeouts={"sage": 0.01})
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            config=config,
        )
        atlas.AGENT_CANCEL_GRACE = 0.01
        cancelled = asyncio.Event()
        release = asyncio.Event()

        async def stubborn_execute(task, context=None):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return {"status": "late"}

        atlas.sage.execute = stubborn_execute

        start = asyncio.get_running_loop().time()
        result = await atlas.delegate("sage", "triage issues")
        elapsed = asyncio.get_running_loop().time() - start

        assert result.success is False
        assert "timed out" in result.error
        assert result.attempts == 1
        assert cancelled.is_set()
        assert elapsed < 0.5

        release.set()
        await asyncio.sleep(0)

    def test_resolve_timeout_editorial_agents_default_to_1800s(
        self, posthog_client, knowledge_base_path
    ):
        """Kai, Mox, Pax run 8-stage editorial pipelines with revision loops that
        routinely exceed 900s on repo-scale prompts (2026-05-08 dogfood)."""
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert atlas._resolve_timeout("kai") == 1800.0
        assert atlas._resolve_timeout("mox") == 1800.0
        assert atlas._resolve_timeout("pax") == 1800.0

    def test_resolve_timeout_other_agents_use_global_default(
        self, posthog_client, knowledge_base_path
    ):
        """Non-editorial agents fall through to the global AGENT_TIMEOUT default."""
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert atlas._resolve_timeout("sage") == 300.0
        assert atlas._resolve_timeout("argus") == 300.0
        assert atlas._resolve_timeout("dex") == 300.0

    def test_resolve_timeout_config_overrides_defaults(self, posthog_client, knowledge_base_path):
        """[orchestration].agent_timeouts in config overrides class defaults."""
        config = AgentConfig(agent_timeouts={"kai": 1200.0, "sage": 60.0})
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            config=config,
        )
        assert atlas._resolve_timeout("kai") == 1200.0  # overridden
        assert atlas._resolve_timeout("sage") == 60.0  # overridden
        assert atlas._resolve_timeout("mox") == 1800.0  # class default still applies
        assert atlas._resolve_timeout("argus") == 300.0  # global default still applies


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
        (archive_dir / f"context_{week_of}_stage1.json").write_text(json.dumps(d, default=str))

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


class TestAtlasArgusStage:
    """Test Argus is wired into the weekly cycle behind the analytics_in_run gate."""

    @pytest.mark.asyncio
    async def test_argus_called_when_analytics_in_run_true(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.analytics_in_run = True

        from datetime import datetime, timezone

        from devrel_origin.core.argus import PerformanceReport

        fake_report = PerformanceReport(
            period_start=datetime(2026, 4, 25, tzinfo=timezone.utc),
            period_end=datetime(2026, 5, 2, tzinfo=timezone.utc),
            top_performers=[],
            bottom_performers=[],
            trend_signals=[],
            recommendations=[],
            sources_ok={"posthog": True, "github": True, "instantly": True, "social": True},
        )
        fake_argus = MagicMock()
        fake_argus.run = AsyncMock(return_value=fake_report)
        atlas._build_argus = MagicMock(return_value=fake_argus)

        await atlas.run_weekly_cycle()
        fake_argus.run.assert_called_once()
        assert atlas.context.argus_report["sources_ok"]["posthog"] is True

    @pytest.mark.asyncio
    async def test_argus_skipped_when_analytics_in_run_false(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.analytics_in_run = False

        fake_argus = MagicMock()
        fake_argus.run = AsyncMock()
        atlas._build_argus = MagicMock(return_value=fake_argus)

        await atlas.run_weekly_cycle()
        fake_argus.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_argus_failure_does_not_abort_cycle(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.analytics_in_run = True

        fake_argus = MagicMock()
        fake_argus.run = AsyncMock(side_effect=RuntimeError("argus down"))
        atlas._build_argus = MagicMock(return_value=fake_argus)

        ctx = await atlas.run_weekly_cycle()
        assert ctx is not None
        assert atlas.context.argus_report.get("error") == "argus down"


class TestAtlasCyraStage:
    """Test Cyra is wired into the weekly cycle as Stage 5c behind the cro_in_run gate."""

    @pytest.mark.asyncio
    async def test_cyra_called_when_cro_in_run_true(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        from devrel_origin.core.cyra import CroReport

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.cro_in_run = True

        fake_report = CroReport(
            period_end="2026-05-07",
            funnel_id="default",
            funnel=[],
            dropoffs=[],
            sources_ok=True,
        )
        fake_cyra = MagicMock()
        fake_cyra.execute = AsyncMock(return_value=fake_report)
        atlas._build_cyra = MagicMock(return_value=fake_cyra)
        atlas._insert_cro_report_row = MagicMock(return_value=1)

        await atlas.run_weekly_cycle()
        fake_cyra.execute.assert_called_once()
        assert atlas.context.cro_report["period_end"] == "2026-05-07"
        assert atlas.context.cro_report["sources_ok"] is True

    @pytest.mark.asyncio
    async def test_cyra_skipped_when_cro_in_run_false(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.cro_in_run = False

        fake_cyra = MagicMock()
        fake_cyra.execute = AsyncMock()
        atlas._build_cyra = MagicMock(return_value=fake_cyra)

        await atlas.run_weekly_cycle()
        fake_cyra.execute.assert_not_called()
        assert atlas.context.cro_report == {}

    @pytest.mark.asyncio
    async def test_cyra_failure_does_not_abort_cycle(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            archive_dir=tmp_path / "archive",
        )
        atlas.config.cro_in_run = True

        fake_cyra = MagicMock()
        fake_cyra.execute = AsyncMock(side_effect=RuntimeError("posthog down"))
        atlas._build_cyra = MagicMock(return_value=fake_cyra)
        atlas._insert_cro_report_row = MagicMock(return_value=0)

        ctx = await atlas.run_weekly_cycle()
        assert ctx is not None
        assert atlas.context.cro_report.get("error") == "posthog down"


class TestSharedContextCroField:
    """Test SharedContext includes the cro_report field."""

    def test_cro_report_field_exists(self):
        ctx = SharedContext()
        assert hasattr(ctx, "cro_report")
        assert ctx.cro_report == {}

    def test_to_dict_includes_cro_report(self):
        ctx = SharedContext()
        d = ctx.to_dict()
        assert "cro_report" in d

    def test_cro_report_round_trips(self):
        ctx = SharedContext()
        ctx.cro_report = {"period_end": "2026-05-07", "sources_ok": True}
        d = ctx.to_dict()
        assert d["cro_report"]["period_end"] == "2026-05-07"


class TestInsertCroReportRow:
    """Test the get-or-insert logic on the analytics_reports anchor row.

    Stage 5c (Cyra) used to insert a fresh row even when Stage 5b (Argus) had
    already written one for the same period_end, leaving two rows where one
    should be. The fix lets _insert_cro_report_row reuse Argus's row id.
    """

    def test_returns_zero_when_db_path_missing(self, tmp_path):
        assert Atlas._insert_cro_report_row(None, "2026-05-08") == 0
        assert Atlas._insert_cro_report_row(tmp_path / "absent.db", "2026-05-08") == 0

    def test_inserts_new_row_when_period_unseen(self, tmp_path):
        db = tmp_path / "state.db"
        project_state.init_db(db)
        rid = Atlas._insert_cro_report_row(db, "2026-05-08")
        assert rid > 0
        with sqlite3.connect(db) as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM analytics_reports WHERE period_end = ?",
                ("2026-05-08",),
            ).fetchone()[0]
        assert cnt == 1

    def test_reuses_existing_row_for_same_period(self, tmp_path):
        """Argus's row must not get a Cyra duplicate alongside it."""
        db = tmp_path / "state.db"
        project_state.init_db(db)
        # Simulate Argus's Stage 5b having written its row first.
        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "INSERT INTO analytics_reports (period_start, period_end, report_json) "
                "VALUES (?, ?, ?)",
                ("2026-05-08", "2026-05-08", '{"argus": "data"}'),
            )
            argus_rid = cur.lastrowid
            conn.commit()

        cyra_rid = Atlas._insert_cro_report_row(db, "2026-05-08")
        assert cyra_rid == argus_rid

        with sqlite3.connect(db) as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM analytics_reports WHERE period_end = ?",
                ("2026-05-08",),
            ).fetchone()[0]
        assert cnt == 1  # Only one row, not two.

    def test_separate_periods_get_separate_rows(self, tmp_path):
        db = tmp_path / "state.db"
        project_state.init_db(db)
        rid_a = Atlas._insert_cro_report_row(db, "2026-05-08")
        rid_b = Atlas._insert_cro_report_row(db, "2026-05-15")
        assert rid_a != rid_b

    def test_argus_report_json_preserved_when_cyra_reuses_row(self, tmp_path):
        """Reusing Argus's row must NOT clobber its report_json blob."""
        db = tmp_path / "state.db"
        project_state.init_db(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO analytics_reports (period_start, period_end, report_json) "
                "VALUES (?, ?, ?)",
                ("2026-05-08", "2026-05-08", '{"argus_findings": "important"}'),
            )
            conn.commit()

        Atlas._insert_cro_report_row(db, "2026-05-08")

        with sqlite3.connect(db) as conn:
            blob = conn.execute(
                "SELECT report_json FROM analytics_reports WHERE period_end = ?",
                ("2026-05-08",),
            ).fetchone()[0]
        assert blob == '{"argus_findings": "important"}'


class TestAtlasContentBriefAndDeliverables:
    def test_build_content_brief_selects_relevant_source_files(
        self, posthog_client, knowledge_base_path
    ):
        atlas = Atlas(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        atlas.context.iris_themes = {
            "themes": [
                {
                    "title": "SDK initialization fails",
                    "description": "Developers hit init errors in the JavaScript SDK.",
                    "severity": 8,
                    "category": "sdk",
                }
            ]
        }
        atlas.context.sage_triage = {
            "issues": [{"number": 101, "title": "SDK init fails", "product_area": "sdk"}]
        }
        atlas.context.dex_docs = {
            "modules": [
                {"path": "frontend/src/sdk/init.ts", "language": "typescript", "symbols": ["init"]},
                {"path": "backend/billing.py", "language": "python", "symbols": ["invoice"]},
            ]
        }

        brief = atlas._build_content_brief()
        assert brief["pain_point"]["title"] == "SDK initialization fails"
        assert brief["github_issues"][0]["number"] == 101
        assert brief["source_files"][0]["path"] == "frontend/src/sdk/init.ts"

    def test_write_weekly_deliverables_persists_content_and_trace(
        self, posthog_client, knowledge_base_path, tmp_path
    ):
        root = tmp_path / "project"
        (root / ".devrel").mkdir(parents=True)
        paths = ProjectPaths.from_root(root)
        paths.deliverables_dir.mkdir(parents=True, exist_ok=True)
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            project_paths=paths,
        )
        atlas.context.week_of = "2026-W18"
        atlas.context.kai_content = {
            "status": "generated",
            "task": "Write about SDK init",
            "content": "# SDK init\n\nBody",
            "grounding_sources": ["sdks/python.md"],
            "revision": {"strengths": ["clear"]},
        }
        atlas.context.dex_docs = {"llm_summary": "Repo summary", "architecture_doc": "Arch"}

        written = atlas._write_weekly_deliverables()
        assert len(written) == 3
        assert (paths.deliverables_dir / "2026-W18" / "write-about-sdk-init.md").exists()
        assert (paths.deliverables_dir / "2026-W18" / "write-about-sdk-init.trace.json").exists()
        assert (paths.deliverables_dir / "2026-W18" / "dex-repository-summary.md").exists()

    def test_compile_okrs_counts_only_generated_content(self, posthog_client, knowledge_base_path):
        atlas = Atlas(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        atlas.context.kai_content = {"status": "insufficient_evidence", "content": ""}
        assert atlas._compile_okrs()["content_produced"] is False

        atlas.context.kai_content = {"status": "generated", "content": "# Draft"}
        assert atlas._compile_okrs()["content_produced"] is True
