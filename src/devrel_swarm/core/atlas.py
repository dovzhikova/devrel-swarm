"""
Atlas — Orchestrator Agent

Coordinates the multi-agent system through task delegation, retry logic,
cross-agent context sharing, and weekly OKR tracking.
"""

import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
from contextlib import nullcontext as _nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from devrel_swarm.project.paths import ProjectPaths
    from devrel_swarm.tools.apollo_client import ApolloClient

from devrel_swarm.core.agent_config import AgentConfig, load_config
from devrel_swarm.core.dex import Dex
from devrel_swarm.core.echo import Echo
from devrel_swarm.core.iris import Iris
from devrel_swarm.core.kai import Kai
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.core.mox import Mox
from devrel_swarm.core.nova import Nova
from devrel_swarm.core.pax import Pax
from devrel_swarm.core.rex import Rex
from devrel_swarm.core.sage import Sage
from devrel_swarm.core.sentinel import Sentinel
from devrel_swarm.core.argus import Argus
from devrel_swarm.core.vox import Vox
from devrel_swarm.core.watchdog import Watchdog
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.github_tools import GitHubTools
from devrel_swarm.tools.instantly_client import InstantlyClient
from devrel_swarm.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


@dataclass
class WeeklyMemory:
    """Summary of a previous week's output for trend detection and dedup."""

    week_of: str = ""
    content_titles: list[str] = field(default_factory=list)
    pain_points_addressed: list[str] = field(default_factory=list)
    competitors_tracked: list[str] = field(default_factory=list)
    experiments_run: list[str] = field(default_factory=list)
    top_themes: list[str] = field(default_factory=list)
    okr_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON and context propagation."""
        return {
            "week_of": self.week_of,
            "content_titles": self.content_titles,
            "pain_points_addressed": self.pain_points_addressed,
            "competitors_tracked": self.competitors_tracked,
            "experiments_run": self.experiments_run,
            "top_themes": self.top_themes,
            "okr_snapshot": self.okr_snapshot,
        }

    @classmethod
    def from_context(cls, ctx: "SharedContext") -> "WeeklyMemory":
        """Extract a compact memory summary from a full SharedContext."""
        content_titles = []
        if isinstance(ctx.kai_content, dict):
            title = ctx.kai_content.get("task", "")
            if title:
                content_titles.append(title)

        pain_points = []
        if isinstance(ctx.iris_themes, dict):
            for t in ctx.iris_themes.get("themes", []):
                if isinstance(t, dict):
                    pain_points.append(t.get("title", ""))

        competitors = []
        if isinstance(ctx.rex_competitive, dict):
            competitors = ctx.rex_competitive.get("competitors_discovered", [])

        experiments = []
        if isinstance(ctx.nova_experiments, dict):
            for e in ctx.nova_experiments.get("experiments", []):
                if isinstance(e, dict):
                    experiments.append(e.get("name", ""))

        return cls(
            week_of=ctx.week_of,
            content_titles=content_titles,
            pain_points_addressed=pain_points[:10],
            competitors_tracked=competitors[:10],
            experiments_run=experiments[:5],
            top_themes=[p for p in pain_points[:5]],
            okr_snapshot=ctx.okr_progress,
        )


@dataclass
class SharedContext:
    """Cross-agent context object that flows between specialists."""

    week_of: str = ""
    sage_triage: dict[str, Any] = field(default_factory=dict)
    echo_social: dict[str, Any] = field(default_factory=dict)
    iris_themes: dict[str, Any] = field(default_factory=dict)
    nova_experiments: dict[str, Any] = field(default_factory=dict)
    kai_content: dict[str, Any] = field(default_factory=dict)
    vox_video: dict[str, Any] = field(default_factory=dict)
    dex_docs: dict[str, Any] = field(default_factory=dict)
    rex_competitive: dict[str, Any] = field(default_factory=dict)
    pax_sales: dict[str, Any] = field(default_factory=dict)
    mox_campaigns: dict[str, Any] = field(default_factory=dict)
    okr_progress: dict[str, Any] = field(default_factory=dict)
    instantly_campaigns: dict[str, Any] = field(default_factory=dict)
    instantly_analytics: dict[str, Any] = field(default_factory=dict)
    instantly_replies: dict[str, Any] = field(default_factory=dict)
    argus_report: dict[str, Any] = field(default_factory=dict)
    previous_weeks: list[WeeklyMemory] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "week_of": self.week_of,
            "sage_triage": self.sage_triage,
            "echo_social": self.echo_social,
            "iris_themes": self.iris_themes,
            "nova_experiments": self.nova_experiments,
            "kai_content": self.kai_content,
            "vox_video": self.vox_video,
            "dex_docs": self.dex_docs,
            "rex_competitive": self.rex_competitive,
            "pax_sales": self.pax_sales,
            "mox_campaigns": self.mox_campaigns,
            "okr_progress": self.okr_progress,
            "instantly_campaigns": self.instantly_campaigns,
            "instantly_analytics": self.instantly_analytics,
            "instantly_replies": self.instantly_replies,
            "argus_report": self.argus_report,
        }
        # previous_weeks included as serialized dicts for downstream agents
        # (not persisted into context archive — save() uses this dict minus previous_weeks)
        if self.previous_weeks:
            d["previous_weeks"] = [w.to_dict() for w in self.previous_weeks]
        return d

    def save(self, archive_dir: Path) -> None:
        """Persist weekly context to archive (excludes transient previous_weeks)."""
        archive_dir.mkdir(parents=True, exist_ok=True)
        filepath = archive_dir / f"context_{self.week_of}.json"
        d = self.to_dict()
        d.pop("previous_weeks", None)  # Don't persist history into archive
        filepath.write_text(json.dumps(d, indent=2, default=str))
        logger.info(f"Archived context to {filepath}")

    @classmethod
    def load(cls, archive_dir: Path) -> "SharedContext":
        """Load the most recent archived context."""
        ctx = cls(week_of=datetime.now().strftime("%Y-W%U"))
        if not archive_dir.exists():
            return ctx
        files = sorted(archive_dir.glob("context_*.json"), reverse=True)
        if not files:
            return ctx
        try:
            data = json.loads(files[0].read_text())
            for key, value in data.items():
                if hasattr(ctx, key) and key != "previous_weeks":
                    setattr(ctx, key, value)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load context from {files[0]}: {e}")
        return ctx

    @classmethod
    def load_with_history(
        cls, archive_dir: Path, history_weeks: int = 4,
    ) -> "SharedContext":
        """Load a fresh context with previous weeks' memory summaries.

        Loads up to *history_weeks* archived contexts, extracts compact
        WeeklyMemory summaries, and attaches them to the new context.
        Downstream agents can use previous_weeks for trend detection,
        content dedup, and continuity.
        """
        ctx = cls(week_of=datetime.now().strftime("%Y-W%U"))
        if not archive_dir.exists():
            return ctx

        files = sorted(archive_dir.glob("context_*.json"), reverse=True)
        memories: list[WeeklyMemory] = []
        for f in files[:history_weeks]:
            try:
                data = json.loads(f.read_text())
                prev = cls()
                for key, value in data.items():
                    if hasattr(prev, key):
                        setattr(prev, key, value)
                memories.append(WeeklyMemory.from_context(prev))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load history from {f}: {e}")

        ctx.previous_weeks = memories
        logger.info(f"Loaded {len(memories)} weeks of history")
        return ctx


@dataclass
class DelegationResult:
    """Result of a delegated task."""

    agent: str
    task: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    attempts: int = 1


class Atlas:
    """
    Orchestrator agent that coordinates the multi-agent system.

    Responsibilities:
    - Delegate tasks to specialist agents
    - Manage cross-agent context sharing
    - Retry failed delegations with exponential backoff
    - Track weekly OKR progress
    - Archive historical context
    """

    MAX_RETRIES = 2
    BASE_DELAY = 2.0  # seconds
    AGENT_TIMEOUT = 300.0  # 5 minutes per agent execution

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        archive_dir: Path = Path("context_archive"),
        llm_client: Optional[LLMClient] = None,
        github_tools: Optional[GitHubTools] = None,
        search_tools: Optional[SearchTools] = None,
        config: Optional[AgentConfig] = None,
        instantly_client: Optional[InstantlyClient] = None,
        apollo_client: Optional["ApolloClient"] = None,
        project_paths: Optional["ProjectPaths"] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.archive_dir = archive_dir
        self.llm_client = llm_client
        self.instantly_client = instantly_client
        self.apollo_client = apollo_client
        self.project_paths = project_paths
        self.config = config or AgentConfig()
        self.context = SharedContext(week_of=datetime.now().strftime("%Y-W%U"))

        # If the caller passed a project_paths and the state DB exists, wire
        # cost events from the LLMClient into the project's `costs` table.
        if (
            project_paths is not None
            and self.llm_client is not None
            and project_paths.state_db.is_file()
        ):
            from devrel_swarm.project.cost_sink import make_sqlite_sink
            self.llm_client.set_cost_sink(make_sqlite_sink(project_paths.state_db))

        # Apply config retry settings
        self.MAX_RETRIES = self.config.retry_settings.get("max_retries", 2)
        self.BASE_DELAY = self.config.retry_settings.get("initial_delay_seconds", 2.0)

        # Initialize specialist agents with shared deps
        self.kai = Kai(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
        )
        self.sage = Sage(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            github_tools=github_tools,
        )
        self.echo = Echo(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=search_tools,
            llm_client=llm_client,
        )
        self.iris = Iris(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
        )
        self.nova = Nova(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
        )
        self.vox = Vox(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=search_tools,
        )
        self.dex = Dex(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
        )
        product_name = self.config.product_name
        self.rex = Rex(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
            apollo_client=apollo_client,
            product_name=product_name,
        )
        self.pax = Pax(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            instantly_client=instantly_client,
            apollo_client=apollo_client,
            product_name=product_name,
        )
        self.mox = Mox(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
            instantly_client=instantly_client,
            product_name=product_name,
        )

        self.watchdog = Watchdog(
            archive_dir=archive_dir,
            llm_client=llm_client,
        )
        self.sentinel = Sentinel(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
        )

        self._agents = {
            "kai": self.kai,
            "sage": self.sage,
            "echo": self.echo,
            "iris": self.iris,
            "nova": self.nova,
            "vox": self.vox,
            "dex": self.dex,
            "rex": self.rex,
            "pax": self.pax,
            "mox": self.mox,
            "watchdog": self.watchdog,
            "sentinel": self.sentinel,
        }

    async def delegate(
        self,
        agent_name: str,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> DelegationResult:
        """
        Delegate a task to a specialist agent with retry logic.

        Uses exponential backoff with jitter on failure.
        """
        agent = self._agents.get(agent_name)
        if not agent:
            return DelegationResult(
                agent=agent_name,
                task=task,
                success=False,
                error=f"Unknown agent: {agent_name}",
            )

        merged_context = {**self.context.to_dict(), **(context or {})}
        last_error = None

        # Tag LLM calls with the agent name for cost tracking
        if self.llm_client:
            self.llm_client.set_agent(agent_name)  # legacy fallback for non-LLM call sites

        for attempt in range(1, self.MAX_RETRIES + 2):
            try:
                logger.info(f"Delegating to {agent_name} (attempt {attempt}): {task[:80]}...")
                ctx_mgr = (
                    self.llm_client.agent_context(agent_name)
                    if self.llm_client
                    else _nullcontext()
                )
                with ctx_mgr:
                    result = await asyncio.wait_for(
                        agent.execute(task=task, context=merged_context),
                        timeout=self.AGENT_TIMEOUT,
                    )
                logger.info(
                    "delegation_success",
                    extra={"agent": agent_name, "task": task[:80], "attempts": attempt},
                )
                return DelegationResult(
                    agent=agent_name,
                    task=task,
                    success=True,
                    output=result,
                    attempts=attempt,
                )
            except asyncio.TimeoutError:
                last_error = f"Agent {agent_name} timed out after {self.AGENT_TIMEOUT}s"
                logger.warning(last_error)
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "delegation_failed",
                    extra={
                        "agent": agent_name,
                        "task": task[:80],
                        "attempt": attempt,
                        "error": last_error,
                    },
                )
                if attempt <= self.MAX_RETRIES:
                    delay = self.BASE_DELAY * (2 ** (attempt - 1))
                    # Add jitter: 50-150% of calculated delay
                    jittered_delay = delay * (0.5 + random.random())
                    await asyncio.sleep(jittered_delay)

        return DelegationResult(
            agent=agent_name,
            task=task,
            success=False,
            error=last_error,
            attempts=self.MAX_RETRIES + 1,
        )

    def _checkpoint(
        self,
        stage: int,
        completed_agents: set[str] | None = None,
    ) -> None:
        """Save a partial checkpoint after completing a pipeline stage.

        Checkpoints are named context_{week}_stage{N}.json and allow
        resuming from the last completed stage on crash recovery.

        ``completed_agents`` is the optional set of agent names that
        finished successfully within (or up to) the current stage —
        used by parallel stages to allow partial-progress resume.
        """
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.archive_dir / f"context_{self.context.week_of}_stage{stage}.json"
        d = self.context.to_dict()
        d.pop("previous_weeks", None)
        d["_checkpoint_stage"] = stage
        d["_completed_agents"] = sorted(completed_agents or [])
        filepath.write_text(json.dumps(d, indent=2, default=str))
        logger.info(f"Checkpoint saved: stage {stage}")

    @classmethod
    def _load_checkpoint(
        cls, archive_dir: Path, week_of: str
    ) -> tuple[int, set[str], SharedContext] | None:
        """Load the latest checkpoint for the current week, if any.

        Returns ``(resume_stage, completed_agents, ctx)`` or ``None``.
        ``completed_agents`` is the set of agents from the partially-
        completed stage that already succeeded; on resume those are
        skipped and only the failed agents are re-run.
        """
        for stage in range(6, -1, -1):
            filepath = archive_dir / f"context_{week_of}_stage{stage}.json"
            if filepath.exists():
                try:
                    data = json.loads(filepath.read_text())
                    ctx = SharedContext(week_of=week_of)
                    for key, value in data.items():
                        if hasattr(ctx, key) and key not in (
                            "_checkpoint_stage",
                            "_completed_agents",
                        ):
                            setattr(ctx, key, value)
                    completed = set(data.get("_completed_agents", []))
                    return data.get("_checkpoint_stage", 0), completed, ctx
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Failed to load checkpoint {filepath}: {e}")
        return None

    def _cleanup_checkpoints(self) -> None:
        """Remove checkpoint files after successful completion."""
        if not self.archive_dir.exists():
            return
        for f in self.archive_dir.glob(f"context_{self.context.week_of}_stage*.json"):
            f.unlink(missing_ok=True)
        logger.info("Cleaned up stage checkpoints")

    async def run_weekly_cycle(self) -> SharedContext:
        """
        Execute the full weekly orchestration cycle with checkpointing.

        Saves progress after each stage group. On restart, resumes from
        the last completed checkpoint instead of re-running everything.
        Produces a run report with timing, cost, and quality data.
        """
        from devrel_swarm.tools.run_report import RunReport
        run_report = RunReport(
            week_of=self.context.week_of,
            started_at=datetime.now().isoformat(),
        )

        # Check for existing checkpoint to resume from
        checkpoint = self._load_checkpoint(self.archive_dir, self.context.week_of)
        resume_stage = 0
        completed_agents: set[str] = set()
        if checkpoint:
            resume_stage, completed_agents, restored = checkpoint
            self.context = restored
            run_report.resumed_from_stage = resume_stage
            logger.info(
                f"Resuming from checkpoint: stage {resume_stage} "
                f"(completed_agents={sorted(completed_agents)})"
            )

        # Load previous weeks' memory for trend detection and dedup
        if resume_stage == 0:
            history_ctx = SharedContext.load_with_history(self.archive_dir)
            self.context.previous_weeks = history_ctx.previous_weeks
        logger.info(
            f"Starting weekly cycle for {self.context.week_of} "
            f"(resume={resume_stage}, history={len(self.context.previous_weeks)} weeks)"
        )

        # Stage 0: Watchdog health check (pre-flight)
        if resume_stage <= 0 and "watchdog" not in completed_agents:
            watchdog_result = await self.delegate(
                "watchdog",
                "Run system health check. Verify all integrations are "
                "reachable and check for stale agent outputs from last cycle.",
            )
            if watchdog_result.success:
                self.context.okr_progress["pre_health"] = watchdog_result.output
                completed_agents.add("watchdog")

        # Stage 1: Sage + Echo + Dex in parallel (no cross-dependencies)
        if resume_stage <= 1:
            stage_1_agents = ["sage", "echo", "dex"]
            stage_1_pending = [a for a in stage_1_agents if a not in completed_agents]
            if stage_1_pending:
                tasks_1 = {
                    "sage": (
                        "Triage GitHub issues from the past 7 days. Categorize by type, "
                        "analyze sentiment, flag churn risks, and identify community champions."
                    ),
                    "echo": (
                        "Scan Reddit, Hacker News, and Twitter/X for OpenClaw mentions. "
                        "Identify engagement opportunities and flag reputation risks."
                    ),
                    "dex": (
                        "Generate technical documentation for the repository. "
                        "Produce an architecture overview and API reference."
                    ),
                }
                coros = [self.delegate(a, tasks_1[a]) for a in stage_1_pending]
                results = await asyncio.gather(*coros)
                for agent_name, res in zip(stage_1_pending, results):
                    if res.success:
                        if agent_name == "sage":
                            self.context.sage_triage = res.output
                        elif agent_name == "echo":
                            self.context.echo_social = res.output
                        elif agent_name == "dex":
                            self.context.dex_docs = res.output
                        completed_agents.add(agent_name)
            self._checkpoint(1, completed_agents=completed_agents)

        # Stage 2: Rex + Iris in parallel (both use Sage + Echo, independent)
        if resume_stage <= 2:
            stage_2_agents = ["rex", "iris"]
            stage_2_pending = [a for a in stage_2_agents if a not in completed_agents]
            if stage_2_pending:
                tasks_2 = {
                    "rex": (
                        "Analyze the competitive landscape. Discover competitors from the "
                        "knowledge base and web search. Identify threats and opportunities."
                    ),
                    "iris": (
                        "Synthesize developer feedback themes from GitHub issues, Discourse "
                        "threads, and support channels. Rank pain points by frequency and "
                        "severity."
                    ),
                }
                coros = [self.delegate(a, tasks_2[a]) for a in stage_2_pending]
                results = await asyncio.gather(*coros)
                for agent_name, res in zip(stage_2_pending, results):
                    if res.success:
                        if agent_name == "rex":
                            self.context.rex_competitive = res.output
                        elif agent_name == "iris":
                            self.context.iris_themes = res.output
                        completed_agents.add(agent_name)
            self._checkpoint(2, completed_agents=completed_agents)

        # Stage 3: Nova + Kai in parallel (both use Iris themes, independent)
        if resume_stage <= 3:
            stage_3_agents = ["nova", "kai"]
            stage_3_pending = [a for a in stage_3_agents if a not in completed_agents]
            if stage_3_pending:
                tasks_3 = {
                    "nova": (
                        "Design activation experiments based on the top pain points. "
                        "Include pre-registration, power analysis, and success criteria."
                    ),
                    "kai": (
                        "Write a technical tutorial addressing the #1 developer pain point. "
                        "Ground the content in the knowledge base and Dex's architecture "
                        "analysis. Reference real GitHub issues from Sage's triage. "
                        "Use actual file paths, commands, and APIs from the source code."
                    ),
                }
                coros = [self.delegate(a, tasks_3[a]) for a in stage_3_pending]
                results = await asyncio.gather(*coros)
                for agent_name, res in zip(stage_3_pending, results):
                    if res.success:
                        if agent_name == "nova":
                            self.context.nova_experiments = res.output
                        elif agent_name == "kai":
                            self.context.kai_content = res.output
                        completed_agents.add(agent_name)
            self._checkpoint(3, completed_agents=completed_agents)

        # Stage 4: Vox (uses Kai's content)
        if resume_stage <= 4 and "vox" not in completed_agents:
            video_result = await self.delegate(
                "vox",
                "Generate a video tutorial from Kai's written content. "
                "Record screen walkthrough with narration and overlays.",
            )
            if video_result.success:
                self.context.vox_video = video_result.output
                completed_agents.add("vox")
            self._checkpoint(4, completed_agents=completed_agents)

        # Stage 5: Sentinel brand audit — audit all generated content
        if resume_stage <= 5 and "sentinel" not in completed_agents:
            sentinel_result = await self.delegate(
                "sentinel",
                "Audit all generated content for brand voice consistency, "
                "ICP alignment, messaging coherence, and technical accuracy.",
            )
            if sentinel_result.success:
                self.context.okr_progress["brand_audit"] = sentinel_result.output
                completed_agents.add("sentinel")
            self._checkpoint(5, completed_agents=completed_agents)

        # Stage 5b: Argus content performance analyst (post-Sentinel, pre-OKR)
        if (
            resume_stage <= 5
            and getattr(self.config, "analytics_in_run", True)
            and "argus" not in completed_agents
        ):
            try:
                argus = self._build_argus()
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=7)
                argus_report = await argus.run(period_start=start, period_end=end)
                self.context.argus_report = argus_report.to_json()
                completed_agents.add("argus")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Argus stage failed (continuing): %s", exc)
                self.context.argus_report = {"error": str(exc)}
            self._checkpoint(5, completed_agents=completed_agents)

        # Stage 6: Instantly sync (analytics + reply triage)
        if (
            resume_stage <= 6
            and self.instantly_client
            and "instantly_sync" not in completed_agents
        ):
            await self._run_instantly_sync()
            completed_agents.add("instantly_sync")
            self._checkpoint(6, completed_agents=completed_agents)

        # Stage 7: OKR compilation (Atlas)
        self.context.okr_progress = self._compile_okrs()

        # Archive the week's context and clean up checkpoints
        self.context.save(self.archive_dir)
        self._cleanup_checkpoints()

        # Self-improvement: extract recurring issues and update agent prompts
        try:
            from devrel_swarm.tools.self_improve import run_self_improvement
        except ImportError as exc:
            logger.warning(
                "Self-improvement module not available; skipping: %s", exc
            )
        else:
            try:
                improve_report = run_self_improvement(
                    self.archive_dir,
                    Path(__file__).parent.parent / "optimize",
                )
                if improve_report.get("recurring_issues"):
                    logger.info(
                        "self_improvement_complete",
                        extra={"agents_updated": list(improve_report["recurring_issues"].keys())},
                    )
            except Exception:
                logger.exception(
                    "Self-improvement step raised; continuing weekly cycle"
                )

        # Stage 8: Publish to content calendar + send notifications
        await self._publish_and_notify()

        # Generate run report
        run_report.completed_at = datetime.now().isoformat()
        started = datetime.fromisoformat(run_report.started_at)
        run_report.duration_seconds = (datetime.now() - started).total_seconds()
        run_report.stages_completed = 8

        if self.llm_client:
            run_report.cost = self.llm_client.usage.to_dict()

        # Quality data from Sentinel and revision traces
        okr = self.context.okr_progress
        quality: dict[str, Any] = {}
        brand_audit = okr.get("brand_audit", {})
        if brand_audit:
            quality["sentinel_score"] = brand_audit.get("overall_score")
        revision_traces: dict[str, Any] = {}
        kai = self.context.kai_content
        if isinstance(kai, dict) and "revision" in kai:
            revision_traces["kai"] = kai["revision"]
        if revision_traces:
            quality["revision_traces"] = revision_traces
        run_report.quality = quality

        health = okr.get("pre_health", {})
        if health:
            run_report.health = health

        run_report.save(self.archive_dir)

        logger.info(
            "weekly_cycle_complete",
            extra={
                "week": self.context.week_of,
                "duration_seconds": run_report.duration_seconds,
                "cost_usd": run_report.cost.get("total_cost_usd", 0),
                "sentinel_score": quality.get("sentinel_score"),
            },
        )
        return self.context

    async def _publish_and_notify(self) -> None:
        """Publish content to calendar and send notifications.

        Gracefully skips if notification/sheets services aren't configured.
        """
        import os

        ctx_dict = self.context.to_dict()

        # Google Sheets content calendar
        sheets_id = os.environ.get("SHEETS_SPREADSHEET_ID", "")
        sheets_token = os.environ.get("SHEETS_ACCESS_TOKEN", "")
        if sheets_id:
            try:
                from devrel_swarm.tools.sheets import ContentCalendar, SheetsConfig
                cal = ContentCalendar(SheetsConfig(
                    spreadsheet_id=sheets_id,
                    access_token=sheets_token,
                ))
                added = await cal.publish_content(ctx_dict)
                logger.info(f"Published to sheets: {added}")
                await cal.close()
            except Exception as exc:
                logger.warning(f"Sheets publish failed: {exc}")

        # Telegram + email notifications
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        email_sender = os.environ.get("EMAIL_SENDER", "")
        if tg_token or email_sender:
            try:
                from devrel_swarm.tools.notifications import NotificationConfig, NotificationService
                svc = NotificationService(NotificationConfig(
                    telegram_bot_token=tg_token,
                    telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
                    email_sender=email_sender,
                    email_password=os.environ.get("EMAIL_PASSWORD", ""),
                    email_recipients=(
                        os.environ.get("EMAIL_RECIPIENTS", "").split(",")
                        if os.environ.get("EMAIL_RECIPIENTS") else None
                    ),
                ))
                result = await svc.send_digest(ctx_dict, mode="weekly")
                logger.info(f"Notifications sent: {result}")
                await svc.close()
            except Exception as exc:
                logger.warning(f"Notifications failed: {exc}")

    async def _run_instantly_sync(self) -> None:
        """Pull Instantly analytics and triage email replies."""
        analytics_result = await self.delegate(
            "mox",
            "Pull campaign analytics from Instantly for all active campaigns.",
        )
        if analytics_result.success:
            self.context.instantly_analytics = analytics_result.output

        triage_result = await self.delegate(
            "pax",
            "Fetch new email replies from Instantly, triage them, "
            "and draft follow-ups for interested leads.",
        )
        if triage_result.success:
            self.context.instantly_replies = triage_result.output

    def _build_argus(self) -> Argus:
        """Construct an Argus instance for the optional Stage 5b call.

        Uses the project state DB (from project_paths) for persistence and WoW
        baselines when available; otherwise runs without persistence.
        """
        from devrel_swarm.tools.analytics import (
            GitHubCollector,
            InstantlyCollector,
            PostHogCollector,
            SocialCollector,
        )

        state_db = (
            self.project_paths.state_db
            if (self.project_paths and self.project_paths.state_db.is_file())
            else None
        )
        social_db = state_db if state_db else Path("/dev/null")

        return Argus(
            posthog_collector=PostHogCollector(self.api_client),
            github_collector=GitHubCollector(self._dummy_github_client()),
            instantly_collector=InstantlyCollector(
                self.instantly_client or self._dummy_instantly_client()
            ),
            social_collector=SocialCollector(social_db),
            llm_client=self.llm_client,
            state_db_path=state_db,
        )

    @staticmethod
    def _dummy_github_client():
        class _Dummy:
            repo_full_name = "unknown/unknown"

            async def get_repo_stats(self):
                raise RuntimeError("github client not configured")

        return _Dummy()

    @staticmethod
    def _dummy_instantly_client():
        class _Dummy:
            async def list_campaigns_with_analytics(self):
                raise RuntimeError("instantly client not configured")

        return _Dummy()

    def _compile_okrs(self) -> dict[str, Any]:
        """Compile weekly OKR progress from all agent outputs."""
        return {
            "week": self.context.week_of,
            "content_produced": bool(self.context.kai_content),
            "issues_triaged": len(self.context.sage_triage.get("issues", [])),
            "social_mentions_found": self.context.echo_social.get("total_mentions", 0),
            "themes_identified": len(self.context.iris_themes.get("themes", [])),
            "experiments_designed": len(self.context.nova_experiments.get("experiments", [])),
            "video_produced": bool(self.context.vox_video),
            "docs_generated": bool(self.context.dex_docs),
            "competitors_analyzed": len(
                self.context.rex_competitive.get("competitors_discovered", [])
            ),
            "emails_sent": self.context.instantly_analytics.get("total_sent", 0),
            "emails_opened": self.context.instantly_analytics.get(
                "total_opened", 0
            ),
            "emails_replied": self.context.instantly_analytics.get(
                "total_replied", 0
            ),
            "reply_rate": self.context.instantly_analytics.get(
                "avg_reply_rate", 0
            ),
            "followups_pending": len(
                self.context.instantly_replies.get("drafts", [])
            ),
            "status": "complete",
        }

    async def run_single_task(self, agent_name: str, task: str) -> DelegationResult:
        """Run a single task on a specific agent (for ad-hoc requests)."""
        return await self.delegate(agent_name, task)


async def process_draft(draft: dict, instantly_client: InstantlyClient) -> str:
    """Process a single follow-up draft interactively.

    Returns: 'approved', 'edited', 'skipped', or 'rejected'
    """
    print(f"\n{'=' * 60}")
    print(f"Category: {draft.get('category', 'unknown')}")
    print(f"To: {draft.get('lead_email', 'unknown')}")
    print(f"Subject: {draft.get('draft_subject', '')}")
    print(f"\n{draft.get('draft_body', '')}")
    print(f"{'=' * 60}")

    choice = input("[a]pprove / [e]dit / [s]kip / [r]eject: ").strip().lower()

    if choice == "a":
        await instantly_client.reply_to_email(
            email_id=draft["email_id"],
            campaign_id=draft.get("campaign_id", ""),
            body=draft["draft_body"],
            thread_id=draft.get("thread_id"),
        )
        draft["status"] = "sent"
        return "approved"
    elif choice == "e":
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write(draft["draft_body"])
            tmp_path = f.name
        editor = os.environ.get("EDITOR", "vi")
        editor_path = shutil.which(editor)
        if editor_path is None:
            logger.warning(
                "EDITOR=%s not found on PATH; skipping interactive edit", editor
            )
            edited_body = draft["draft_body"]
        else:
            subprocess.run([editor_path, str(tmp_path)], check=False)
            with open(tmp_path) as f:
                edited_body = f.read()
        os.unlink(tmp_path)
        await instantly_client.reply_to_email(
            email_id=draft["email_id"],
            campaign_id=draft.get("campaign_id", ""),
            body=edited_body,
            thread_id=draft.get("thread_id"),
        )
        draft["status"] = "sent"
        return "edited"
    elif choice == "r":
        draft["status"] = "rejected"
        return "rejected"
    else:
        return "skipped"


def _build_apollo_client(api_key: Optional[str]) -> Optional["ApolloClient"]:
    """Instantiate ApolloClient when the API key is available, else return None."""
    if not api_key:
        return None
    from devrel_swarm.tools.apollo_client import ApolloClient  # noqa: PLC0415
    return ApolloClient(api_key=api_key)


async def _run_review_replies(instantly_client: Optional[InstantlyClient]) -> None:
    """Handle the --review-replies CLI mode."""
    archive_dir = Path("context_archive")
    ctx = SharedContext.load(archive_dir)
    drafts = ctx.instantly_replies.get("drafts", [])
    pending = [d for d in drafts if d.get("status") == "pending_approval"]

    if not pending:
        print("No pending follow-up drafts to review.")
        return

    if not instantly_client:
        print("Error: INSTANTLY_API_KEY not set. Cannot send replies.")
        return

    print(f"\n{len(pending)} pending follow-up(s) to review:\n")
    stats: dict[str, int] = {"approved": 0, "edited": 0, "skipped": 0, "rejected": 0}

    for draft in pending:
        result = await process_draft(draft, instantly_client)
        stats[result] = stats.get(result, 0) + 1

    print(
        f"\nDone! {stats['approved']} approved, "
        f"{stats['edited']} edited, "
        f"{stats['skipped']} skipped, "
        f"{stats['rejected']} rejected"
    )


async def main():
    """CLI entry point for running the orchestrator."""
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Atlas Orchestrator Agent")
    parser.add_argument(
        "--weekly-cycle",
        action="store_true",
        help="Run the full weekly orchestration cycle",
    )
    parser.add_argument("--agent", type=str, help="Target agent for single task")
    parser.add_argument("--task", type=str, help="Task description")
    parser.add_argument(
        "--review-replies",
        action="store_true",
        help="Review and approve pending follow-up email drafts",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/agent_config.yaml",
        help="Path to agent config YAML",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))

    client = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )
    kb_path = Path(__file__).parent.parent / "knowledge_base"

    llm_client = (
        LLMClient(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            budget_limit_usd=config.budget_limit_usd,
        )
        if os.environ.get("ANTHROPIC_API_KEY")
        else None
    )

    github_tools = (
        GitHubTools(
            token=os.environ.get("GITHUB_TOKEN", ""),
        )
        if os.environ.get("GITHUB_TOKEN")
        else None
    )

    search = SearchTools(
        firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
    )

    instantly_client = (
        InstantlyClient(api_key=os.environ.get("INSTANTLY_API_KEY", ""))
        if os.environ.get("INSTANTLY_API_KEY")
        else None
    )

    apollo_client = _build_apollo_client(os.environ.get("APOLLO_API_KEY"))

    atlas = Atlas(
        api_client=client,
        knowledge_base_path=kb_path,
        llm_client=llm_client,
        github_tools=github_tools,
        search_tools=search,
        config=config,
        instantly_client=instantly_client,
        apollo_client=apollo_client,
    )

    try:
        if args.review_replies:
            await _run_review_replies(instantly_client)
            return
        elif args.weekly_cycle:
            context = await atlas.run_weekly_cycle()
            print(json.dumps(context.to_dict(), indent=2, default=str))
        elif args.agent and args.task:
            result = await atlas.run_single_task(args.agent, args.task)
            print(json.dumps(result.__dict__, indent=2, default=str))
        else:
            parser.print_help()
    finally:
        if llm_client:
            await llm_client.close()
        if github_tools:
            await github_tools.close()
        if apollo_client:
            await apollo_client.close()
        if instantly_client:
            await instantly_client.close()
        await search.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
