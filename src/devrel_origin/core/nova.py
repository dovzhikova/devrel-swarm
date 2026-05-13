"""
Nova — Growth Strategist Agent

Designs activation experiments, analyzes funnels, segments cohorts,
and models LTV — all with statistical rigor.
"""

import hashlib
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from scipy import stats

from devrel_origin.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)


# Default daily signups assumed when DAILY_SIGNUPS_ESTIMATE is unset.
DAILY_SIGNUPS_DEFAULT = 500
# Below this floor, experiment durations explode into multi-decade ranges
# and a value of 0 raises ZeroDivisionError. We clamp to this floor and
# warn rather than silently producing a 30-year duration.
DAILY_SIGNUPS_FLOOR = 10


@dataclass
class ExperimentDesign:
    """A pre-registered A/B test design with statistical rigor."""

    experiment_id: str
    hypothesis: str
    primary_metric: str
    secondary_metrics: list[str]
    control_description: str
    variant_description: str
    sample_size_per_arm: int
    minimum_detectable_effect: float  # e.g., 0.05 for 5% lift
    statistical_power: float  # typically 0.8
    significance_level: float  # typically 0.05
    expected_duration_days: int
    guardrail_metrics: list[str]
    evaluation_method: str  # frequentist, bayesian
    pre_registration_date: str
    success_criteria: str


@dataclass
class FunnelAnalysis:
    """Analysis of a conversion funnel."""

    funnel_name: str
    stages: list[dict[str, Any]]  # name, count, conversion_rate, drop_off
    overall_conversion: float
    biggest_drop_off_stage: str
    recommended_interventions: list[str]


@dataclass
class CohortSegment:
    """A user cohort defined by behavior and attributes."""

    segment_name: str
    definition: str
    size: int
    activation_rate: float
    retention_d7: float
    retention_d30: float
    avg_events_per_user: float
    ltv_estimate: float


class Nova:
    """
    Growth Strategist agent for experiment design and funnel optimization.

    Capabilities:
    - Design A/B experiments with proper power analysis and pre-registration
    - Analyze activation and conversion funnels
    - Segment users into behavioral cohorts
    - Model LTV by segment
    - Recommend growth interventions based on data

    Tools:
    1. analytics_query — Query analytics for trends, funnels, retention
    2. experiments_api — Create and read experiments via API
    3. cohorts_api — Define and query cohorts
    4. feature_flags_api — Manage feature flags for experiments
    5. power_calculator — Compute required sample size for experiments
    6. bayesian_evaluator — Evaluate experiment results with Bayesian methods
    7. funnel_analyzer — Decompose funnels into stage-by-stage metrics
    8. cohort_segmenter — Cluster users by behavior patterns
    9. ltv_modeler — Estimate lifetime value by segment
    10. intervention_recommender — Suggest growth actions based on funnel data
    11. experiment_pre_registrar — Generate pre-registration documents
    12. statistical_validator — Check experiment results for common pitfalls
    13. report_generator — Compile experiment results into reports
    14. alert_configurator — Set up metric alerts for guardrails
    """

    SYSTEM_PROMPT = """You are Nova, a growth strategist for OpenClaw. You design
experiments and analyze data with statistical rigor to drive developer activation
and retention.

Growth principles:
1. PRE-REGISTER — Define hypothesis, metrics, and sample size BEFORE running
2. POWER ANALYSIS — Never run underpowered experiments. Calculate required n.
3. GUARDRAILS — Every experiment needs guardrail metrics to catch regressions
4. BAYESIAN WHEN POSSIBLE — Bayesian evaluation for faster, more intuitive results
5. SEGMENT FIRST — Different cohorts have different activation patterns

OpenClaw activation metrics:
- Time to repo cloned and dependencies installed (< 5 min = good)
- Time to first agent run completed (< 30 min = good)
- Knowledge base configured in first 7 days (>= 1 vertical = activated)
- Weekly cycle activated in first 7 days (>= 1 full cycle = activated)
- Team members onboarded in first 14 days (>= 1 additional = sticky)

Key funnel stages:
1. Signup → Repo cloned (target: 70%)
2. Repo cloned → First agent run (target: 85%)
3. First agent run → Knowledge base configured (target: 50%)
4. Knowledge base configured → Weekly cycle activated (target: 30%)
5. Weekly cycle activated → Team onboarded (target: 40%)

Power analysis formula:
n = (Z_alpha/2 + Z_beta)^2 * 2 * p * (1-p) / MDE^2
where MDE = minimum detectable effect, p = baseline conversion rate"""

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.info(f"Nova executing: {task[:80]}...")

        themes = []
        if context and "iris_themes" in context:
            iris_data = context["iris_themes"]
            if isinstance(iris_data, dict):
                themes = iris_data.get("themes", [])

        # Design experiments for top themes
        experiments = []
        for theme in themes[:3]:
            title = theme.get("title", "Unknown")
            severity = theme.get("severity", 5.0)
            areas = theme.get("product_areas", ["general"])
            # High-severity themes warrant detecting a smaller lift (3% MDE) — the
            # downside of missing a real improvement is large because the underlying
            # pain is hurting users. Lower-severity themes accept a larger MDE (5%)
            # to ship faster; if the experiment is inconclusive, the cost of being
            # wrong is bounded.
            mde = 0.03 if severity >= 7 else 0.05
            baseline = 0.15

            exp = await self.design_experiment(
                hypothesis=f"Addressing '{title}' will improve activation",
                primary_metric=f"{areas[0]}_activation_rate",
                baseline_rate=baseline,
                minimum_detectable_effect=mde,
            )
            experiments.append(
                {
                    "experiment_id": exp.experiment_id,
                    "hypothesis": exp.hypothesis,
                    "primary_metric": exp.primary_metric,
                    "sample_size_per_arm": exp.sample_size_per_arm,
                    "expected_duration_days": exp.expected_duration_days,
                    "success_criteria": exp.success_criteria,
                    "guardrail_metrics": exp.guardrail_metrics,
                }
            )

        # Analyze the standard OpenClaw activation funnel.
        # Attempt to source real funnel stages from the API client when
        # available; otherwise fall back to default illustrative estimates
        # and mark them as such so downstream consumers know not to trust
        # the absolute counts.
        funnel_result = None
        if themes:
            default_stages = [
                {"name": "signup", "count": 1000},
                {"name": "repo_cloned", "count": 700},
                {"name": "first_agent_run", "count": 595},
                {"name": "knowledge_base_configured", "count": 298},
                {"name": "weekly_cycle_activated", "count": 89},
                {"name": "team_onboarded", "count": 36},
            ]
            stages = default_stages
            funnel_data_source = "default_estimates"
            if self.api_client is not None and hasattr(self.api_client, "get_funnel"):
                try:
                    real_stages = await self.api_client.get_funnel()
                    if real_stages:
                        stages = real_stages
                        funnel_data_source = "api"
                except Exception as exc:
                    logger.warning("Funnel API call failed; using default estimates: %s", exc)
            funnel = await self.analyze_funnel(
                funnel_name="devrel_ai_agents_activation",
                stages=stages,
            )
            funnel_result = {
                "funnel_name": funnel.funnel_name,
                "data_source": funnel_data_source,
                "overall_conversion": funnel.overall_conversion,
                "biggest_drop_off_stage": funnel.biggest_drop_off_stage,
                "recommended_interventions": funnel.recommended_interventions,
            }

        return {
            "agent": "nova",
            "task": task,
            "experiments": experiments,
            "funnel_analysis": funnel_result,
            "cohort_segments": [],
            "upstream_themes_used": len(themes),
            "status": "designed",
        }

    def calculate_sample_size(
        self,
        baseline_rate: float,
        minimum_detectable_effect: float,
        power: float = 0.8,
        significance_level: float = 0.05,
    ) -> int:
        """
        Calculate required sample size per arm for a two-proportion z-test.

        Args:
            baseline_rate: Current conversion rate (e.g., 0.15 for 15%)
            minimum_detectable_effect: Absolute change to detect (e.g., 0.03 for 3pp)
            power: Statistical power (default 0.8)
            significance_level: Alpha level (default 0.05)

        Returns:
            Required sample size per arm
        """
        z_alpha = stats.norm.ppf(1 - significance_level / 2)
        z_beta = stats.norm.ppf(power)

        p1 = baseline_rate
        p2 = baseline_rate + minimum_detectable_effect
        p_avg = (p1 + p2) / 2

        n = (
            z_alpha * math.sqrt(2 * p_avg * (1 - p_avg))
            + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
        ) ** 2 / minimum_detectable_effect**2

        return math.ceil(n)

    async def design_experiment(
        self,
        hypothesis: str,
        primary_metric: str,
        baseline_rate: float,
        minimum_detectable_effect: float,
        context: Optional[dict[str, Any]] = None,
    ) -> ExperimentDesign:
        """Design a fully pre-registered A/B experiment."""
        sample_size = self.calculate_sample_size(
            baseline_rate=baseline_rate,
            minimum_detectable_effect=minimum_detectable_effect,
        )

        # Estimate duration based on daily traffic volume. Clamp to a
        # floor so misconfigured envs (or test environments setting 0)
        # don't produce 30-year experiments or ZeroDivisionError.
        raw_signups = int(os.environ.get("DAILY_SIGNUPS_ESTIMATE", str(DAILY_SIGNUPS_DEFAULT)))
        if raw_signups < DAILY_SIGNUPS_FLOOR:
            logger.warning(
                "DAILY_SIGNUPS_ESTIMATE=%d is below floor %d; using floor instead",
                raw_signups,
                DAILY_SIGNUPS_FLOOR,
            )
            daily_signups = DAILY_SIGNUPS_FLOOR
        else:
            daily_signups = raw_signups
        duration_days = math.ceil((sample_size * 2) / daily_signups)

        return ExperimentDesign(
            # sha256-based ID is stable across process restarts (Python's
            # built-in hash() is randomized per-process, breaking
            # pre-registration de-duplication).
            experiment_id=f"exp_{hashlib.sha256(hypothesis.encode()).hexdigest()[:8]}",
            hypothesis=hypothesis,
            primary_metric=primary_metric,
            secondary_metrics=["time_to_first_event", "d7_retention"],
            control_description="Current experience (no changes)",
            variant_description="See experiment hypothesis",
            sample_size_per_arm=sample_size,
            minimum_detectable_effect=minimum_detectable_effect,
            statistical_power=0.8,
            significance_level=0.05,
            expected_duration_days=duration_days,
            guardrail_metrics=["error_rate", "page_load_time", "sdk_init_time"],
            evaluation_method="bayesian",
            pre_registration_date="",
            success_criteria=(
                f"Reject null hypothesis at alpha=0.05 with "
                f">={minimum_detectable_effect * 100:.1f}pp lift in {primary_metric}"
            ),
        )

    async def analyze_funnel(
        self,
        funnel_name: str,
        stages: list[dict[str, Any]],
    ) -> FunnelAnalysis:
        """Analyze a conversion funnel and recommend interventions."""
        # Calculate conversion rates and find biggest drop-off
        for i, stage in enumerate(stages):
            if i == 0:
                stage["conversion_rate"] = 1.0
            else:
                prev_count = stages[i - 1]["count"]
                stage["conversion_rate"] = stage["count"] / prev_count if prev_count > 0 else 0
            stage["drop_off"] = 1 - stage["conversion_rate"]

        biggest_drop = max(stages[1:], key=lambda s: s["drop_off"])
        overall = stages[-1]["count"] / stages[0]["count"] if stages[0]["count"] > 0 else 0

        return FunnelAnalysis(
            funnel_name=funnel_name,
            stages=stages,
            overall_conversion=overall,
            biggest_drop_off_stage=biggest_drop["name"],
            recommended_interventions=[
                f"Investigate drop-off at '{biggest_drop['name']}' stage "
                f"({biggest_drop['drop_off'] * 100:.1f}% drop-off)",
                "Run qualitative research with users who dropped off at this stage",
                "Design an experiment to reduce friction at this stage",
            ],
        )
