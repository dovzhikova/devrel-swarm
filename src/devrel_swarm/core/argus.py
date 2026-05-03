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
import sqlite3
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
    """One optimization recommendation tied to one target.

    ``source_ids`` is the list of ``content_id`` values that back this
    recommendation. v1 uses these only for display; v2 (closed-loop routing)
    uses them so Iris/Mox/Nova can resolve the rec to actionable artifacts
    without re-parsing the free-text ``target``.
    """

    action: RecAction
    target: str
    target_type: TargetType
    rationale: str
    evidence: list[str]
    confidence: float
    source_ids: list[str] = field(default_factory=list)


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

    def to_json(self) -> dict:
        return _report_to_jsonable(self)

    def to_markdown(self) -> str:
        return _render_markdown(self)


def _metric_to_jsonable(m: PerformanceMetric) -> dict:
    return {
        "content_id": m.content_id,
        "content_type": m.content_type,
        "title": m.title,
        "url": m.url,
        "published_at": m.published_at.isoformat(),
        "primary_metric": m.primary_metric,
        "metric_name": m.metric_name,
        "secondary_metrics": dict(m.secondary_metrics),
        "percentile": m.percentile,
        "wow_delta": m.wow_delta,
        "anomaly_flag": m.anomaly_flag,
    }


def _rec_to_jsonable(r: Recommendation) -> dict:
    return {
        "action": r.action,
        "target": r.target,
        "target_type": r.target_type,
        "rationale": r.rationale,
        "evidence": list(r.evidence),
        "confidence": r.confidence,
        "source_ids": list(r.source_ids),
    }


def _report_to_jsonable(r: PerformanceReport) -> dict:
    return {
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "top_performers": [_metric_to_jsonable(m) for m in r.top_performers],
        "bottom_performers": [_metric_to_jsonable(m) for m in r.bottom_performers],
        "trend_signals": list(r.trend_signals),
        "recommendations": [_rec_to_jsonable(rec) for rec in r.recommendations],
        "sources_ok": dict(r.sources_ok),
        "insufficient_data": r.insufficient_data,
        "llm_error": r.llm_error,
    }


_REC_ACTION_ORDER: tuple[str, ...] = (
    "double_down", "amplify", "rewrite", "retest", "retire", "investigate",
)


def _render_markdown(report: PerformanceReport) -> str:
    lines: list[str] = []
    start = report.period_start.date().isoformat()
    end = report.period_end.date().isoformat()
    lines.append(f"# Argus Performance Report — {start} to {end}")
    lines.append("")

    lines.append("## Source health")
    for source, ok in report.sources_ok.items():
        lines.append(f"- {source}: {'ok' if ok else 'failed'}")
    if report.llm_error:
        lines.append(f"- llm: failed ({report.llm_error})")
    if report.insufficient_data:
        lines.append("")
        lines.append(
            "> **Insufficient data** — too little signal for trustworthy recommendations."
        )
    lines.append("")

    lines.append("## Top performers")
    if not report.top_performers:
        lines.append("_None this period._")
    for m in report.top_performers:
        pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
        lines.append(
            f"- **{m.content_id}** ({m.content_type}) — "
            f"{m.primary_metric:g} {m.metric_name} ({pct})"
        )
    lines.append("")

    lines.append("## Bottom performers")
    if not report.bottom_performers:
        lines.append("_None this period._")
    for m in report.bottom_performers:
        pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
        lines.append(
            f"- **{m.content_id}** ({m.content_type}) — "
            f"{m.primary_metric:g} {m.metric_name} ({pct})"
        )
    lines.append("")

    lines.append("## Trend signals")
    if not report.trend_signals:
        lines.append("_None._")
    for sig in report.trend_signals:
        lines.append(f"- {sig}")
    lines.append("")

    lines.append("## Recommendations")
    if not report.recommendations:
        lines.append("_No recommendations this period._")
    else:
        grouped: dict[str, list[Recommendation]] = {}
        for r in report.recommendations:
            grouped.setdefault(r.action, []).append(r)
        for action in _REC_ACTION_ORDER:
            bucket = grouped.get(action, [])
            if not bucket:
                continue
            lines.append(f"### {action} ({len(bucket)})")
            for r in bucket:
                lines.append(
                    f"- **{r.target}** (conf {r.confidence:.2f}) — {r.rationale}"
                )
                if r.source_ids:
                    lines.append(f"  - sources: {', '.join(r.source_ids)}")
                for ev in r.evidence:
                    lines.append(f"  - evidence: {ev}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _score_metrics(
    metrics: list[PerformanceMetric],
    *,
    baseline_by_id: dict[str, float],
) -> list[PerformanceMetric]:
    """Annotate each metric with percentile, wow_delta, and anomaly_flag.

    Pure function — input metrics are not mutated; new instances are returned.

    - percentile: rank within same content_type peers (0..100, 100 = best)
    - wow_delta: % change vs baseline_by_id[content_id], None if no baseline
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

            baseline = baseline_by_id.get(m.content_id)
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
        self._system_prompt = load_agent_prompt(
            "argus", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT,
        )

    async def run(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> PerformanceReport:
        """Pull, score, recommend, persist. Returns the PerformanceReport."""
        period = (period_start, period_end)
        all_metrics, sources_ok = await self._gather(period)

        if not all_metrics:
            logger.info(
                "argus.run: insufficient_data — no metrics from any source",
                extra={
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "sources_ok": sources_ok,
                },
            )
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

        baseline = await self._load_baselines() if self.state_db_path else {}
        logger.info(
            "argus.baselines_loaded",
            extra={"baseline_count": len(baseline)},
        )
        scored = _score_metrics(all_metrics, baseline_by_id=baseline)
        anomaly_count = sum(1 for m in scored if m.anomaly_flag)
        logger.info(
            "argus.scored",
            extra={
                "scored_count": len(scored),
                "anomaly_count": anomaly_count,
                "content_types": sorted({m.content_type for m in scored}),
            },
        )

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
        logger.info(
            "argus.recommendations_generated",
            extra={
                "recs_count": len(recs),
                "trend_signals_count": len(trend_signals),
                "llm_error": llm_error,
            },
        )

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
            await self._persist(report, scored)
            logger.info(
                "argus.persisted",
                extra={
                    "period_end": period_end.isoformat(),
                    "metric_history_rows": len(scored),
                },
            )

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
        logger.info(
            "argus.gather_complete",
            extra={
                "ok_sources": sorted(k for k, v in sources_ok.items() if v),
                "failed_sources": sorted(k for k, v in sources_ok.items() if not v),
                "total_metrics": len(all_metrics),
            },
        )
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

    async def _load_baselines(self) -> dict[str, float]:
        """Async wrapper that delegates SQLite read to a thread."""
        if not self.state_db_path or not self.state_db_path.is_file():
            return {}
        return await asyncio.to_thread(self._load_baselines_sync)

    def _load_baselines_sync(self) -> dict[str, float]:
        """Read the most recent prior period's primary_metric per content_id.

        Used by ``_score_metrics`` for week-over-week deltas. Reads from the
        indexed ``metric_history`` table when available (single SELECT, no
        JSON deserialization). Falls back to the legacy ``all_primary`` blob
        and then to top/bottom slices for reports written before either
        existed. Returns {} when the DB has no prior data.
        """
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Prefer indexed metric_history. Pick the most recent period
                # and pull all content_id rows from it.
                latest = conn.execute(
                    "SELECT MAX(period_end) AS p FROM metric_history"
                ).fetchone()
                if latest and latest["p"]:
                    rows = conn.execute(
                        "SELECT content_id, primary_metric FROM metric_history "
                        "WHERE period_end = ?",
                        (latest["p"],),
                    ).fetchall()
                    if rows:
                        return {r["content_id"]: float(r["primary_metric"]) for r in rows}

                # Fallback: legacy blob in analytics_reports.
                row = conn.execute(
                    "SELECT report_json FROM analytics_reports "
                    "ORDER BY period_end DESC LIMIT 1"
                ).fetchone()
        except sqlite3.OperationalError:
            return {}
        if not row:
            return {}
        try:
            data = json.loads(row["report_json"])
        except json.JSONDecodeError:
            return {}
        all_primary = data.get("all_primary")
        if isinstance(all_primary, dict) and all_primary:
            return {cid: float(v) for cid, v in all_primary.items()}
        baseline: dict[str, float] = {}
        for section in ("top_performers", "bottom_performers"):
            for entry in data.get(section, []):
                cid = entry.get("content_id")
                if cid:
                    baseline[cid] = float(entry.get("primary_metric", 0.0))
        return baseline

    async def _persist(
        self, report: PerformanceReport, all_metrics: list[PerformanceMetric],
    ) -> None:
        """Async wrapper that delegates the SQLite write to a thread."""
        if not self.state_db_path:
            return
        await asyncio.to_thread(self._persist_sync, report, all_metrics)

    def _persist_sync(
        self, report: PerformanceReport, all_metrics: list[PerformanceMetric],
    ) -> None:
        """Serialize the full report to three tables in one transaction:

        - ``analytics_reports``: human-readable JSON archive
        - ``metric_history``: indexed (content_id, period_end) time-series for
          baseline lookups
        - ``analytics_recommendations``: per-rec rows for v2 routing
          (queryable by action/target without parsing the report blob)

        ``first_seen_period`` is set to this report's period_end on insert;
        a future "lifecycle" pass will fold in earlier first-seen values.
        """
        payload = report.to_json()
        payload["all_primary"] = {m.content_id: m.primary_metric for m in all_metrics}
        period_end_iso = report.period_end.isoformat()
        with sqlite3.connect(self.state_db_path) as conn:
            cur = conn.execute(
                "INSERT INTO analytics_reports "
                "(period_start, period_end, report_json) VALUES (?, ?, ?)",
                (
                    report.period_start.isoformat(),
                    period_end_iso,
                    json.dumps(payload),
                ),
            )
            report_id = cur.lastrowid
            conn.executemany(
                "INSERT OR REPLACE INTO metric_history "
                "(content_id, period_end, primary_metric, metric_name, content_type) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (m.content_id, period_end_iso, m.primary_metric,
                     m.metric_name, m.content_type)
                    for m in all_metrics
                ],
            )
            if report.recommendations:
                conn.executemany(
                    "INSERT INTO analytics_recommendations "
                    "(report_id, period_end, action, target, target_type, "
                    "rationale, confidence, source_ids_json, evidence_json, "
                    "first_seen_period) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            report_id, period_end_iso,
                            r.action, r.target, r.target_type,
                            r.rationale, r.confidence,
                            json.dumps(list(r.source_ids)),
                            json.dumps(list(r.evidence)),
                            period_end_iso,
                        )
                        for r in report.recommendations
                    ],
                )
            conn.commit()

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
        return self._system_prompt

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
        types_dropped: list[tuple[str, int]] = []  # (content_type, item_count)
        for ctype, group in by_type.items():
            if total >= 50:
                # Whole content type dropped — record so the prompt notes it
                types_dropped.append((ctype, len(group)))
                continue
            ranked = sorted(group, key=lambda m: m.primary_metric, reverse=True)
            slice_ = ranked[:10] + (ranked[-5:] if len(ranked) > 10 else [])
            metric_name = ranked[0].metric_name if ranked else "n/a"
            section_lines = [
                f"### {ctype.upper()} ({len(group)} items, primary metric: {metric_name})"
            ]
            shown = 0
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
                shown += 1
            if shown < len(slice_):
                # Partial section — note how many were truncated
                omitted = len(slice_) - shown
                section_lines.append(
                    f"- ... ({omitted} more {ctype} items omitted from this section)"
                )
            sections.append("\n".join(section_lines))

        if types_dropped:
            dropped_summary = ", ".join(
                f"{ctype} ({n} items)" for ctype, n in types_dropped
            )
            sections.append(
                f"### TRUNCATED\nEntire content types omitted from prompt: "
                f"{dropped_summary}"
            )

        leaderboard = "\n\n".join(sections)
        user_prompt = f"""Period leaderboard (top 10 + bottom 5 per content type):

{leaderboard}

Return a JSON object with two top-level keys:
- "recommendations": array of {{action, target, target_type, rationale, evidence, confidence, source_ids}}
- "trend_signals": array of short strings describing themes/channel patterns (3-7 items)

action ∈ {{double_down, retire, rewrite, retest, amplify, investigate}}
target_type ∈ {{content, theme, channel}}
confidence ∈ [0.0, 1.0]; below 0.5 use action="investigate".
source_ids: array of content_id strings from the leaderboard above that back this
recommendation (use the exact content_id values; min 1, max 5). For target_type="theme"
or "channel", list the exemplary content_ids that motivated the recommendation.

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
                source_ids=list(r.get("source_ids", [])),
            )
            for r in data.get("recommendations", [])
        ]
        trend_signals = list(data.get("trend_signals", []))
        return recs, trend_signals
