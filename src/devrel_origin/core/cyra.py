"""Cyra: CRO (conversion rate optimization) auditor.

Pulls funnel time-series from PostHog, identifies the step with the worst
week-over-week drop-off, and asks Sonnet for 3 ICE-scored A/B test
hypotheses per worst-step. Emits Recommendation rows to the shared
analytics_recommendations table for Mox to materialize as test variants.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devrel_origin.core.base import strip_markdown_fences
from devrel_origin.core.growth import (
    Pillar,
    Recommendation,
    TargetKind,
    persist_recommendation,
)
from devrel_origin.core.llm import LLMClient
from devrel_origin.tools.api_client import PostHogClient

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


_HYPOTHESIS_PROMPT = """You are a CRO analyst. Drop-off detected on a conversion funnel:

  Step: {from_step} -> {to_step}
  Sample size: {sample_size:,}
  Current conversion: {current_rate:.1%}
  Week-over-week change: {pp_delta:+.1%}

Page HTML (truncated to 4KB):

{page_html}

User-reported friction (from Sage):
{sage_friction}

Recurring themes (from Iris):
{iris_themes}

Generate exactly {n} A/B test hypotheses. Return JSON only:

{{
  "hypotheses": [
    {{
      "title": "<short imperative, <=80 chars>",
      "rationale": "<2 sentences: why this drop is happening + why this test should help>",
      "impact": <1-10, expected lift if winner>,
      "confidence": <1-10, certainty in the hypothesis>,
      "effort": <1-10, dev work required (lower = less)>
    }}
  ]
}}

Score impact/confidence/effort honestly. False confidence skews ICE rankings.
Return ONLY the JSON, no markdown fences, no explanation."""


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

    async def _compute_dropoffs(
        self,
        *,
        funnel: list[str],
        days: int = 7,
    ) -> list[DropOff]:
        """Compute step-by-step drop-offs comparing current vs prior period.

        Returns dropoffs sorted by `absolute_drop` desc (largest drop first).
        WoW deterioration flagged when current pp - prior pp <= -0.05.
        """
        if len(funnel) < 2:
            return []

        current = await self.posthog.funnel_query(events=funnel, days=days)
        prior = await self.posthog.funnel_query(events=funnel, days=days * 2)

        dropoffs: list[DropOff] = []
        for i in range(len(current) - 1):
            from_step = current[i]
            to_step = current[i + 1]
            conv = (to_step["count"] / from_step["count"]) if from_step["count"] else 0.0

            # Prior-period conversion rate for the same step
            if len(prior) > i + 1 and prior[i]["count"]:
                prior_conv = prior[i + 1]["count"] / prior[i]["count"]
            else:
                prior_conv = conv  # no baseline -> pp_delta = 0

            dropoffs.append(
                DropOff(
                    from_step=from_step["name"],
                    to_step=to_step["name"],
                    from_count=from_step["count"],
                    to_count=to_step["count"],
                    conversion_rate=conv,
                    # round to 10dp to avoid IEEE 754 drift at the -0.05 significance boundary
                    pp_delta_vs_prior=round(conv - prior_conv, 10),
                    sample_size=from_step["count"],
                )
            )

        return sorted(dropoffs, key=lambda d: d.absolute_drop, reverse=True)

    async def _generate_hypotheses(
        self,
        *,
        dropoff: DropOff,
        page_html: str,
        iris_themes: list[str] | None = None,
        sage_friction: list[str] | None = None,
    ) -> list[Hypothesis]:
        """Ask Sonnet for `hypothesis_count` ICE-scored A/B hypotheses."""
        prompt = _HYPOTHESIS_PROMPT.format(
            from_step=dropoff.from_step,
            to_step=dropoff.to_step,
            sample_size=dropoff.sample_size,
            current_rate=dropoff.conversion_rate,
            pp_delta=dropoff.pp_delta_vs_prior,
            page_html=page_html[:4000],
            iris_themes="\n".join(f"- {t}" for t in (iris_themes or [])) or "(none)",
            sage_friction="\n".join(f"- {f}" for f in (sage_friction or [])) or "(none)",
            n=self.hypothesis_count,
        )

        text = await self.llm.generate(
            system_prompt="You are a CRO analyst.",
            user_prompt=prompt,
            temperature=0.4,
            max_tokens=1500,
        )
        # Strip any stray fences (defensive: the model is told not to use them)
        text = strip_markdown_fences(text)
        data = json.loads(text)

        hyps = [
            Hypothesis(
                title=h["title"],
                rationale=h["rationale"],
                impact=int(h["impact"]),
                confidence=int(h["confidence"]),
                effort=int(h["effort"]),
            )
            for h in data["hypotheses"]
        ]
        return sorted(hyps, key=lambda h: h.ice_score, reverse=True)

    async def _cohort_split(
        self,
        *,
        funnel: list[str],
        segments: list[tuple[str, str]],
        days: int = 7,
    ) -> dict[str, dict]:
        """Per-segment conversion breakdown for the funnel.

        `segments` is a list of (utm_source, device_type) pairs. Suppresses
        segments below `self.min_sample_size`.
        """
        breakdown: dict[str, dict] = {}
        for utm_source, device_type in segments:
            # In a real implementation we'd add `properties` filters to the
            # funnel query for utm_source and device_type. For Wave 1 we
            # follow the contract: the test stubs return per-segment data.
            steps = await self.posthog.funnel_query(events=funnel, days=days)
            if not steps:
                continue
            sample = steps[0]["count"]
            if sample < self.min_sample_size:
                continue
            conv = (steps[-1]["count"] / sample) if sample else 0.0
            key = f"{utm_source}|{device_type}"
            breakdown[key] = {
                "sample_size": sample,
                "conversion_rate": conv,
                "final_count": steps[-1]["count"],
            }
        return breakdown

    def _action_for_dropoff(self, dropoff: DropOff) -> str:
        """Map a drop-off pattern to an action verb.

        - significant deterioration (>=5pp WoW worse): 'retest'
        - significant improvement (>=5pp better): 'double_down'
        - low sample without trend or everything else: 'investigate'
        """
        if dropoff.is_significant_deterioration:
            return "retest"
        if dropoff.pp_delta_vs_prior >= 0.05:
            return "double_down"
        return "investigate"

    def _db_writable(self) -> bool:
        """True iff self.db_path points at a real SQLite file we can write to.

        The Atlas Stage 5c fallback constructs Cyra with `Path("/dev/null")` when
        no project_paths is available; recommendations and funnel metrics then
        have nowhere to land. Detect that up-front so persist methods no-op
        cleanly instead of FK-violating or corrupting /dev/null.
        """
        return self.db_path is not None and self.db_path.is_file()

    def _persist_funnel_metrics(self, report: CroReport) -> None:
        """Write one cro_funnel_metrics row per FunnelStep.

        Without this, `devrel cro history` and `devrel cro diff` (which both
        query cro_funnel_metrics) return empty rows even after Cyra has run.
        INSERT OR REPLACE is intentional: re-running on the same period
        replaces stale conversion-rate snapshots with the freshest read.
        """
        if not self._db_writable() or not report.funnel:
            return
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO cro_funnel_metrics "
                "(funnel_id, step_index, period_end, conversion_rate, sample_size, "
                "segment_breakdown_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        report.funnel_id,
                        step.index,
                        report.period_end,
                        step.conversion_rate,
                        step.count,
                        "{}",
                    )
                    for step in report.funnel
                ],
            )
            conn.commit()

    def _persist(self, report: CroReport, *, report_id: int) -> None:
        """Convert dropoffs + hypotheses into Recommendation rows and append to report.

        No-ops when the analytics_reports anchor row is missing (report_id <= 0)
        or the project state DB isn't writable: persist_recommendation would
        otherwise FK-violate under PRAGMA foreign_keys=ON. Funnel metrics are
        independent and persist via _persist_funnel_metrics regardless.
        """
        if report_id <= 0 or not self._db_writable():
            logger.warning(
                "Cyra: skipping recommendation persistence (report_id=%d, db_writable=%s)",
                report_id,
                self._db_writable(),
            )
            return
        for d in report.dropoffs:
            action = self._action_for_dropoff(d)
            hypotheses = report.hypotheses_by_step.get(d.to_step, [])
            # Encode hypotheses into source_ids (denormalized; cheap)
            source_ids = [json.dumps(h.to_dict()) for h in hypotheses]
            confidence = (
                sum(h.confidence for h in hypotheses) / (10 * len(hypotheses))
                if hypotheses
                else 0.5
            )
            rec = Recommendation(
                pillar=Pillar.CRO,
                action=action,
                target=d.to_step,
                target_kind=TargetKind.FUNNEL_STEP,
                confidence=confidence,
                source_ids=source_ids,
                first_seen_period=report.period_end,
            )
            persist_recommendation(self.db_path, report_id, rec)
            report.recommendations.append(rec)

    def _write_briefs(self, report: CroReport, deliverables_dir: Path) -> None:
        """Write one .md brief per recommendation for Mox to pick up."""
        deliverables_dir.mkdir(parents=True, exist_ok=True)
        for rec in report.recommendations:
            hypotheses = report.hypotheses_by_step.get(rec.target, [])
            dropoff = next(
                (d for d in report.dropoffs if d.to_step == rec.target),
                None,
            )

            md_lines = [
                f"# Cyra brief: {rec.action} `{rec.target}`",
                "",
                f"**Period:** {report.period_end}",
                "**Pillar:** cro",
                f"**Funnel:** {report.funnel_id}",
                f"**Confidence:** {rec.confidence:.2f}",
                "",
                "## Drop-off context",
                "",
            ]
            if dropoff:
                md_lines.extend(
                    [
                        f"- From step: `{dropoff.from_step}` ({dropoff.from_count:,} users)",
                        f"- To step: `{dropoff.to_step}` ({dropoff.to_count:,} users)",
                        f"- Conversion rate: {dropoff.conversion_rate:.1%}",
                        f"- WoW delta: {dropoff.pp_delta_vs_prior:+.1%}",
                        "",
                    ]
                )

            if hypotheses:
                md_lines.extend(
                    [
                        "## A/B hypotheses (ICE-ranked)",
                        "",
                        "| Title | Impact | Confidence | Effort | ICE | Rationale |",
                        "|-------|-------:|-----------:|-------:|----:|-----------|",
                    ]
                )
                for h in hypotheses:
                    md_lines.append(
                        f"| {h.title} | {h.impact} | {h.confidence} | {h.effort} | "
                        f"{h.ice_score:.1f} | {h.rationale} |"
                    )
                md_lines.append("")

            md_lines.extend(
                [
                    "## Next steps",
                    "",
                    "- Mox: pick the highest-ICE hypothesis above and draft the test variant.",
                    "- Nova: validate sample-size + duration for the planned uplift target.",
                    "",
                ]
            )

            slug = rec.target.replace("/", "-").replace(" ", "-")
            path = deliverables_dir / f"cro-brief-{report.period_end}-{rec.action}-{slug}.md"
            path.write_text("\n".join(md_lines))
            logger.info(f"Cyra wrote brief: {path}")

    async def execute(
        self,
        *,
        period_end: str,
        report_id: int,
        page_html_by_url: dict[str, str] | None = None,
        iris_themes: list[str] | None = None,
        sage_friction: list[str] | None = None,
        deliverables_dir: Path | None = None,
    ) -> CroReport:
        """Run a full Cyra cycle.

        Stages: detect funnel, compute dropoffs, hypothesize worst step,
        persist, write briefs. `page_html_by_url` keys by step name (e.g.,
        'signup_started'); when a step matches, we feed the corresponding
        HTML into the hypothesis prompt.
        """
        page_html_by_url = page_html_by_url or {}
        iris_themes = iris_themes or []
        sage_friction = sage_friction or []

        funnel = await self._autodetect_funnel(days=7)
        if not funnel:
            return CroReport(
                period_end=period_end,
                funnel_id=self.funnel_id,
                funnel=[],
                dropoffs=[],
                sources_ok=False,
            )

        dropoffs = await self._compute_dropoffs(funnel=funnel, days=7)
        if not dropoffs:
            return CroReport(
                period_end=period_end,
                funnel_id=self.funnel_id,
                funnel=[],
                dropoffs=[],
                sources_ok=True,
            )

        # Build FunnelStep view for the report.
        # Look up dropoffs by from_step (dropoffs are sorted by absolute_drop, not funnel order).
        dropoff_by_from = {d.from_step: d for d in dropoffs}
        first_count = dropoff_by_from[funnel[0]].from_count if funnel[0] in dropoff_by_from else 0
        funnel_steps: list[FunnelStep] = []
        for i, ev in enumerate(funnel):
            if i == 0:
                funnel_steps.append(
                    FunnelStep(name=ev, index=0, count=first_count, conversion_rate=1.0)
                )
            else:
                prev_event = funnel[i - 1]
                d = dropoff_by_from.get(prev_event)
                if d is None:
                    # No dropoff found for this transition (unexpected; fall back to 0)
                    count = 0
                    conversion_rate = 0.0
                else:
                    count = d.to_count
                    conversion_rate = (count / first_count) if first_count else 0.0
                funnel_steps.append(
                    FunnelStep(name=ev, index=i, count=count, conversion_rate=conversion_rate)
                )

        # Hypothesize the worst-deterioration step (or worst absolute drop if no deterioration)
        worst = next((d for d in dropoffs if d.is_significant_deterioration), dropoffs[0])
        hypotheses_by_step: dict[str, list[Hypothesis]] = {}
        try:
            page_html = page_html_by_url.get(worst.to_step, "")
            hyps = await self._generate_hypotheses(
                dropoff=worst,
                page_html=page_html,
                iris_themes=iris_themes,
                sage_friction=sage_friction,
            )
            hypotheses_by_step[worst.to_step] = hyps
        except Exception as e:
            logger.warning(f"Cyra: hypothesis generation failed: {e}")

        report = CroReport(
            period_end=period_end,
            funnel_id=self.funnel_id,
            funnel=funnel_steps,
            dropoffs=dropoffs,
            hypotheses_by_step=hypotheses_by_step,
        )

        # Funnel metrics persist independently of report_id (no FK to
        # analytics_reports), so devrel cro history/diff can render trends
        # even when the recommendations path no-ops on a missing report row.
        self._persist_funnel_metrics(report)
        self._persist(report, report_id=report_id)
        if deliverables_dir is not None:
            self._write_briefs(report, deliverables_dir)

        return report
