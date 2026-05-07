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
from pathlib import Path  # noqa: F401 -- used for report output paths in Task 10+
from typing import Optional  # noqa: F401 -- used by Cyra attrs in Tasks 3-9

from devrel_swarm.core.growth import (
    Pillar,  # noqa: F401 -- used in Task 7 recommendation generation
    Recommendation,
    TargetKind,  # noqa: F401 -- used in Task 7 recommendation generation
    persist_recommendation,  # noqa: F401 -- used in Task 7
)
from devrel_swarm.core.llm import LLMClient  # noqa: F401 -- used by Cyra in Task 5
from devrel_swarm.tools.api_client import PostHogClient  # noqa: F401 -- used by Cyra in Task 3

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


class Cyra:
    """CRO auditor agent. Defined fully in Tasks 3-9 of the Wave 1 plan."""
