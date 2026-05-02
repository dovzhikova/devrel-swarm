"""
Argus — Content Performance Analyst Agent.

Pulls post-publish performance data from PostHog, GitHub, Instantly, and
Echo's social_mentions table; ranks content deterministically; and emits
structured optimization recommendations via a single Sonnet call.

Sits beside Watchdog (infra) and Sentinel (pre-publish) as the
post-publish watcher in the 13-agent pantheon.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from devrel_swarm.core.base import load_agent_prompt, strip_markdown_fences

logger = logging.getLogger(__name__)

_ANOMALY_Z_THRESHOLD = 2.5

ContentType = Literal["blog", "landing", "social", "email", "repo", "video"]
RecAction = Literal[
    "double_down", "retire", "rewrite", "retest", "amplify", "investigate",
]
TargetType = Literal["content", "theme", "channel"]


@dataclass
class PerformanceMetric:
    """Single content piece's performance snapshot for one period."""

    content_id: str
    content_type: ContentType
    title: str
    url: str | None
    published_at: datetime
    primary_metric: float
    metric_name: str
    secondary_metrics: dict[str, float] = field(default_factory=dict)
    percentile: float | None = None
    wow_delta: float | None = None
    anomaly_flag: bool = False


@dataclass
class Recommendation:
    """One optimization recommendation tied to one target."""

    action: RecAction
    target: str
    target_type: TargetType
    rationale: str
    evidence: list[str]
    confidence: float


@dataclass
class PerformanceReport:
    """Full Argus run output. Serialized to .devrel/state.db and to markdown."""

    period_start: datetime
    period_end: datetime
    top_performers: list[PerformanceMetric]
    bottom_performers: list[PerformanceMetric]
    trend_signals: list[str]
    recommendations: list[Recommendation]
    sources_ok: dict[str, bool]
    insufficient_data: bool = False
    llm_error: str | None = None


def _score_metrics(
    metrics: list[PerformanceMetric],
    *,
    baseline_by_type: dict[str, float],
) -> list[PerformanceMetric]:
    """Annotate each metric with percentile, wow_delta, and anomaly_flag.

    Pure function — input metrics are not mutated; new instances are returned.

    - percentile: rank within same content_type peers (0..100, 100 = best)
    - wow_delta: % change vs baseline_by_type[content_id], None if no baseline
    - anomaly_flag: |z-score| > _ANOMALY_Z_THRESHOLD against group mean/stdev
    """
    by_type: dict[str, list[PerformanceMetric]] = {}
    for m in metrics:
        by_type.setdefault(m.content_type, []).append(m)

    out: list[PerformanceMetric] = []
    for group in by_type.values():
        values = [m.primary_metric for m in group]
        n = len(values)
        mean = statistics.fmean(values) if values else 0.0
        stdev = statistics.pstdev(values) if n > 1 else 0.0

        for m in group:
            if n <= 1:
                pct = 100.0
            else:
                lower = sum(1 for v in values if v < m.primary_metric)
                pct = (lower / (n - 1)) * 100.0

            baseline = baseline_by_type.get(m.content_id)
            if baseline is None or baseline == 0:
                wow = None
            else:
                wow = ((m.primary_metric - baseline) / baseline) * 100.0

            anomaly = False
            if stdev > 0:
                z = (m.primary_metric - mean) / stdev
                anomaly = abs(z) > _ANOMALY_Z_THRESHOLD

            out.append(
                PerformanceMetric(
                    content_id=m.content_id,
                    content_type=m.content_type,
                    title=m.title,
                    url=m.url,
                    published_at=m.published_at,
                    primary_metric=m.primary_metric,
                    metric_name=m.metric_name,
                    secondary_metrics=dict(m.secondary_metrics),
                    percentile=round(pct, 2),
                    wow_delta=round(wow, 2) if wow is not None else None,
                    anomaly_flag=anomaly,
                )
            )
    return out


class Argus:
    """Content performance analyst.

    Orchestrates four collectors in parallel, scores metrics deterministically,
    and asks a Sonnet LLM to generate structured Recommendation objects from
    the ranked leaderboard. Per-collector failures are isolated and surfaced in
    PerformanceReport.sources_ok rather than aborting the whole report.
    """

    def __init__(
        self,
        posthog_collector,
        github_collector,
        instantly_collector,
        social_collector,
        llm_client: Optional[Any] = None,
        state_db_path: Optional[Path] = None,
    ):
        self._collectors = {
            "posthog": posthog_collector,
            "github": github_collector,
            "instantly": instantly_collector,
            "social": social_collector,
        }
        self.llm_client = llm_client
        self.state_db_path = state_db_path

    async def run(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> PerformanceReport:
        """Pull, score, recommend, persist. Returns the PerformanceReport."""
        period = (period_start, period_end)
        all_metrics, sources_ok = await self._gather(period)

        if not all_metrics:
            return PerformanceReport(
                period_start=period_start,
                period_end=period_end,
                top_performers=[],
                bottom_performers=[],
                trend_signals=[],
                recommendations=[],
                sources_ok=sources_ok,
                insufficient_data=True,
            )

        baseline = self._load_baselines() if self.state_db_path else {}
        scored = _score_metrics(all_metrics, baseline_by_type=baseline)

        top, bottom = self._top_bottom(scored)

        recs: list[Recommendation] = []
        trend_signals: list[str] = []
        llm_error: Optional[str] = None
        if self.llm_client:
            try:
                recs, trend_signals = await self._generate_recommendations(scored)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Argus LLM step failed: %s", exc)
                llm_error = str(exc)

        report = PerformanceReport(
            period_start=period_start,
            period_end=period_end,
            top_performers=top,
            bottom_performers=bottom,
            trend_signals=trend_signals,
            recommendations=recs,
            sources_ok=sources_ok,
            llm_error=llm_error,
        )

        if self.state_db_path:
            self._persist(report)

        return report

    async def _gather(
        self, period: tuple[datetime, datetime],
    ) -> tuple[list[PerformanceMetric], dict[str, bool]]:
        """Run all four collectors in parallel; isolate per-source failures."""
        names = list(self._collectors.keys())
        coros = [c.collect(period) for c in self._collectors.values()]
        results = await asyncio.gather(*coros, return_exceptions=True)

        all_metrics: list[PerformanceMetric] = []
        sources_ok: dict[str, bool] = {}
        for name, result in zip(names, results, strict=True):
            if isinstance(result, Exception):
                sources_ok[name] = False
                logger.warning("Argus collector %s raised: %s", name, result)
            else:
                sources_ok[name] = True
                all_metrics.extend(result)
        return all_metrics, sources_ok

    @staticmethod
    def _top_bottom(
        scored: list[PerformanceMetric],
    ) -> tuple[list[PerformanceMetric], list[PerformanceMetric]]:
        """Top 5 and bottom 3 per content_type, flattened."""
        by_type: dict[str, list[PerformanceMetric]] = {}
        for m in scored:
            by_type.setdefault(m.content_type, []).append(m)
        top: list[PerformanceMetric] = []
        bottom: list[PerformanceMetric] = []
        for group in by_type.values():
            ranked = sorted(group, key=lambda m: m.primary_metric, reverse=True)
            top.extend(ranked[:5])
            bottom.extend(list(reversed(ranked[-3:])))
        return top, bottom

    def _load_baselines(self) -> dict[str, float]:
        """Stub — populated in Task 10."""
        return {}

    def _persist(self, report: PerformanceReport) -> None:
        """Stub — populated in Task 10."""
        return

    _DEFAULT_SYSTEM_PROMPT = """You are Argus, a content performance analyst. \
Given a ranked leaderboard of content with engagement metrics, you produce \
structured optimization recommendations.

Your action vocabulary is closed. Use exactly one of:
- double_down: theme/channel is winning; produce more of this kind of content
- retire: content/theme is consistently underperforming; stop investing
- rewrite: specific piece has potential but is poorly executed; redo it
- retest: result is inconclusive; re-run with more samples or a different cohort
- amplify: already-good content is under-distributed; push harder on existing channels
- investigate: anomaly you cannot confidently explain; flag for human review

Be evidence-based. Every recommendation must cite specific metrics with content_ids.
Bias toward fewer, higher-confidence recommendations. Five strong recs beat fifteen weak ones.
Confidence below 0.5 means "investigate" — do not recommend a directional action."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        return load_agent_prompt(
            "argus", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT,
        )

    async def _generate_recommendations(
        self,
        scored: list[PerformanceMetric],
    ) -> tuple[list[Recommendation], list[str]]:
        """One Sonnet call. Returns (recommendations, trend_signals).

        Bounded input: top 10 + bottom 5 per content type, capped at 50 lines.
        Output: JSON with ``recommendations`` and ``trend_signals`` arrays.
        """
        by_type: dict[str, list[PerformanceMetric]] = {}
        for m in scored:
            by_type.setdefault(m.content_type, []).append(m)

        sections: list[str] = []
        total = 0
        for ctype, group in by_type.items():
            ranked = sorted(group, key=lambda m: m.primary_metric, reverse=True)
            slice_ = ranked[:10] + (ranked[-5:] if len(ranked) > 10 else [])
            metric_name = ranked[0].metric_name if ranked else "n/a"
            section_lines = [
                f"### {ctype.upper()} ({len(group)} items, primary metric: {metric_name})"
            ]
            for m in slice_:
                if total >= 50:
                    break
                pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
                wow = f", wow {m.wow_delta:+.1f}%" if m.wow_delta is not None else ""
                anom = " [ANOMALY]" if m.anomaly_flag else ""
                section_lines.append(
                    f"- {m.content_id}: {m.primary_metric:g} {m.metric_name} "
                    f"({pct}{wow}){anom} — {m.title}"
                )
                total += 1
            sections.append("\n".join(section_lines))
            if total >= 50:
                break

        leaderboard = "\n\n".join(sections)
        user_prompt = f"""Period leaderboard (top 10 + bottom 5 per content type):

{leaderboard}

Return a JSON object with two top-level keys:
- "recommendations": array of {{action, target, target_type, rationale, evidence, confidence}}
- "trend_signals": array of short strings describing themes/channel patterns (3-7 items)

action ∈ {{double_down, retire, rewrite, retest, amplify, investigate}}
target_type ∈ {{content, theme, channel}}
confidence ∈ [0.0, 1.0]; below 0.5 use action="investigate".

Do not include any commentary outside the JSON."""

        raw = await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=2048,
        )
        cleaned = strip_markdown_fences(raw).strip()
        data = json.loads(cleaned)
        recs = [
            Recommendation(
                action=r["action"],
                target=r["target"],
                target_type=r["target_type"],
                rationale=r["rationale"],
                evidence=list(r.get("evidence", [])),
                confidence=float(r["confidence"]),
            )
            for r in data.get("recommendations", [])
        ]
        trend_signals = list(data.get("trend_signals", []))
        return recs, trend_signals
