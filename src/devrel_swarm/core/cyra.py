"""Cyra: CRO (conversion rate optimization) auditor.

Pulls funnel time-series from PostHog, identifies the step with the worst
week-over-week drop-off, and asks Sonnet for 3 ICE-scored A/B test
hypotheses per worst-step. Emits Recommendation rows to the shared
analytics_recommendations table for Mox to materialize as test variants.
"""

from __future__ import annotations

import asyncio  # noqa: F401 -- used by Cyra.execute() in Tasks 3-9
import json  # noqa: F401 -- used by LLM response parsing in Task 5
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime  # noqa: F401 -- used in Tasks 3-9 for period calculations
from pathlib import Path
from typing import Optional  # noqa: F401 -- used by Cyra attrs in Tasks 3-9

from devrel_swarm.core.growth import (
    Pillar,  # noqa: F401 -- used in Task 7 recommendation generation
    Recommendation,
    TargetKind,  # noqa: F401 -- used in Task 7 recommendation generation
    persist_recommendation,  # noqa: F401 -- used in Task 7
)
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)


@dataclass
class FunnelStep:
    name: str
    index: int
    count: int
    conversion_rate: float  # share of users who reached this step from step 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FunnelStep":
        return cls(**d)


@dataclass
class DropOff:
    from_step: str
    to_step: str
    from_count: int
    to_count: int
    conversion_rate: float  # share of from_count that progressed
    pp_delta_vs_prior: float  # percentage-point change WoW; negative = worse
    sample_size: int  # = from_count

    @property
    def absolute_drop(self) -> int:
        return self.from_count - self.to_count

    @property
    def is_significant_deterioration(self) -> bool:
        return self.pp_delta_vs_prior <= -0.05  # >=5pp worse than prior period


@dataclass
class Hypothesis:
    title: str
    rationale: str
    impact: int  # 1-10
    confidence: int  # 1-10
    effort: int  # 1-10 (lower = less effort)

    @property
    def ice_score(self) -> float:
        return (self.impact * self.confidence) / max(self.effort, 1)

    def to_dict(self) -> dict:
        return {**asdict(self), "ice_score": self.ice_score}


@dataclass
class CroReport:
    period_end: str
    funnel_id: str
    funnel: list[FunnelStep]
    dropoffs: list[DropOff]
    hypotheses_by_step: dict[str, list[Hypothesis]] = field(default_factory=dict)
    recommendations: list[Recommendation] = field(default_factory=list)
    sources_ok: bool = True


# PostHog system events to exclude from auto-detected funnels.
# Defense-in-depth: the $-prefix filter below already excludes these, but
# this set is kept in case PostHog introduces non-$-prefixed system events.
_SYSTEM_EVENTS = frozenset(
    {
        "$identify",
        "$pageleave",
        "$autocapture",
        "$rageclick",
        "$set",
        "$create_alias",
        "$opt_in",
        "$exception",
    }
)


class Cyra:
    """CRO auditor agent.

    Inputs: PostHog (funnel time-series), optional Iris/Sage priors for
    hypothesis ranking, optional `[growth].cro_funnel` override.

    Outputs: Recommendation rows in `analytics_recommendations` (pillar=cro),
    Mox-ready briefs at `.devrel/deliverables/cro-brief-*.md`.
    """

    def __init__(
        self,
        *,
        posthog_client: PostHogClient,
        llm_client: LLMClient,
        db_path: Path,
        funnel_override: list[str] | None = None,
        funnel_id: str = "default",
        min_sample_size: int = 500,
        hypothesis_count: int = 3,
    ):
        self.posthog = posthog_client
        self.llm = llm_client
        self.db_path = db_path
        self.funnel_override = funnel_override or []
        self.funnel_id = funnel_id
        self.min_sample_size = min_sample_size
        self.hypothesis_count = hypothesis_count

    async def _autodetect_funnel(self, days: int = 7) -> list[str]:
        """Pick the highest-volume `$pageview` to custom_event chain.

        Heuristic: first event is always `$pageview` (top of funnel for any
        web product); subsequent events are the top non-system events by
        volume in descending order. We cap the chain at 5 steps:
        deeper funnels rarely have enough sample size at the tail.
        """
        if self.funnel_override:
            return self.funnel_override

        volumes = await self.posthog.event_volumes(days=days, limit=50)
        custom = [
            (name, count)
            for name, count in volumes
            if name not in _SYSTEM_EVENTS and not name.startswith("$")
        ]
        if not custom:
            logger.warning("Cyra: no custom events found in PostHog; funnel auto-detect failed")
            return []

        return ["$pageview"] + [name for name, _ in custom[:4]]
