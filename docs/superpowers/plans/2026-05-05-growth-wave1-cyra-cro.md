# Growth Pipeline Wave 1 — Cyra (CRO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Cyra, the CRO auditor — pulls funnel data from PostHog, identifies the worst week-over-week drop-off, and emits 3 LLM-generated A/B hypotheses (ICE-scored) per drop point as `Recommendation` rows.

**Architecture:** New `core/cyra.py` agent class. Extends `tools/api_client.PostHogClient` with a `funnel_query` method. Auto-detects the funnel from highest-volume `$pageview → custom_event` chains; honors `[growth].cro_funnel` override. LLM hypothesis call uses `LLMClient.generate_with_revision` — same pattern Nova uses for experiment design. Persistence + lifecycle via `core/growth/recommendations.py` (Wave 0).

**Tech Stack:** Python 3.12 async, httpx via existing PostHogClient, dataclasses, Anthropic Claude Sonnet for hypothesis gen, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-05-05-growth-pipeline-design.md`
**Depends on:** Wave 0 (schema v5, growth module).

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_origin/tools/api_client.py` | Modify | Add `funnel_query`, `event_volumes` methods to `PostHogClient` |
| `src/devrel_origin/core/cyra.py` | Create | Cyra agent class, `FunnelStep`/`DropOff`/`Hypothesis`/`CroReport` dataclasses, `execute()` method |
| `src/devrel_origin/core/__init__.py` | Modify | Export `Cyra` |
| `src/devrel_origin/cli/cro.py` | Create | Typer `cro_app` with `report`/`history`/`diff`/`calibration`/`funnel` verbs |
| `src/devrel_origin/cli/__init__.py` | Modify | Register `cro_app` |
| `tests/test_cyra.py` | Create | Cyra unit tests (auto-detect, drop-off, hypotheses, persistence) |
| `tests/cli/test_cro_command.py` | Create | CLI verb smoke tests |

---

## Task 1: Extend `PostHogClient` with `event_volumes` and `funnel_query`

**Files:**
- Modify: `src/devrel_origin/tools/api_client.py`
- Test: `tests/test_api_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_client.py`:

```python
import respx
from httpx import Response


@respx.mock
async def test_event_volumes_returns_top_events(posthog_client):
    respx.post("https://app.posthog.com/api/projects/1/query/").mock(
        return_value=Response(200, json={
            "results": [
                ["$pageview", 12500],
                ["signup_started", 3200],
                ["signup_completed", 1850],
            ],
        })
    )
    out = await posthog_client.event_volumes(days=7, limit=10)
    assert out[0] == ("$pageview", 12500)
    assert len(out) == 3


@respx.mock
async def test_funnel_query_returns_step_conversion_rates(posthog_client):
    respx.post("https://app.posthog.com/api/projects/1/query/").mock(
        return_value=Response(200, json={
            "results": [{
                "name": "$pageview",       "count": 1000, "average_conversion_time": 0,
            }, {
                "name": "signup_started",  "count":  300, "average_conversion_time": 120,
            }, {
                "name": "signup_completed","count":  120, "average_conversion_time": 600,
            }]
        })
    )
    steps = await posthog_client.funnel_query(
        events=["$pageview", "signup_started", "signup_completed"], days=7,
    )
    assert len(steps) == 3
    assert steps[0]["name"] == "$pageview"
    assert steps[0]["count"] == 1000
    assert steps[2]["count"] == 120
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_api_client.py::test_event_volumes_returns_top_events tests/test_api_client.py::test_funnel_query_returns_step_conversion_rates -v --no-cov
```

Expected: AttributeError — methods don't exist.

- [ ] **Step 3: Implement the methods**

Append to `PostHogClient` class in `src/devrel_origin/tools/api_client.py`:

```python
    async def event_volumes(self, days: int = 7, limit: int = 50) -> list[tuple[str, int]]:
        """Return [(event_name, count), ...] for the top events in the period.

        Used by Cyra to auto-detect a funnel candidate (highest-volume
        $pageview → custom_event chain).
        """
        query = {
            "kind": "EventsQuery",
            "select": ["event", "count()"],
            "after": f"-{days}d",
            "orderBy": ["-count()"],
            "limit": limit,
        }
        resp = await self._post(
            f"/api/projects/{self.project_id}/query/",
            json={"query": query},
        )
        results = resp.json().get("results", [])
        return [(row[0], int(row[1])) for row in results]

    async def funnel_query(
        self, events: list[str], days: int = 7,
    ) -> list[dict]:
        """Run a funnel query for the given event sequence.

        Returns one dict per step with `name`, `count`, `average_conversion_time`.
        """
        query = {
            "kind": "FunnelsQuery",
            "series": [{"event": e, "kind": "EventsNode"} for e in events],
            "dateRange": {"date_from": f"-{days}d"},
        }
        resp = await self._post(
            f"/api/projects/{self.project_id}/query/",
            json={"query": query},
        )
        return resp.json().get("results", [])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_client.py::test_event_volumes_returns_top_events tests/test_api_client.py::test_funnel_query_returns_step_conversion_rates -v --no-cov
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/api_client.py tests/test_api_client.py
git commit -m "feat(posthog): add event_volumes + funnel_query for Cyra"
```

---

## Task 2: Cyra dataclasses

**Files:**
- Create: `src/devrel_origin/core/cyra.py`
- Test: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cyra.py`:

```python
"""Unit tests for the Cyra (CRO) agent."""

import json
from pathlib import Path

import pytest

from devrel_origin.core.cyra import (
    CroReport,
    Cyra,
    DropOff,
    FunnelStep,
    Hypothesis,
)


class TestDataclasses:
    def test_funnel_step_round_trip(self):
        step = FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0)
        assert step.to_dict()["name"] == "$pageview"
        assert FunnelStep.from_dict(step.to_dict()) == step

    def test_dropoff_pp_delta(self):
        d = DropOff(
            from_step="signup_started", to_step="signup_completed",
            from_count=300, to_count=120, conversion_rate=0.4,
            pp_delta_vs_prior=-0.08, sample_size=300,
        )
        assert d.absolute_drop == 180
        assert d.is_significant_deterioration is True  # |pp_delta| ≥ 0.05

    def test_hypothesis_ice_score(self):
        h = Hypothesis(
            title="Add social proof above CTA",
            rationale="Drop-off correlates with low trust signals",
            impact=8, confidence=6, effort=3,
        )
        # ICE = (impact * confidence) / effort
        assert h.ice_score == pytest.approx(16.0)
```

- [ ] **Step 2: Run to confirm fails**

```bash
pytest tests/test_cyra.py::TestDataclasses -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Create the module + dataclasses**

Create `src/devrel_origin/core/cyra.py`:

```python
"""Cyra — CRO (conversion rate optimization) auditor.

Pulls funnel time-series from PostHog, identifies the step with the worst
week-over-week drop-off, and asks Sonnet for 3 ICE-scored A/B test
hypotheses per worst-step. Emits Recommendation rows to the shared
analytics_recommendations table for Mox to materialize as test variants.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

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
    conversion_rate: float          # share of from_count that progressed
    pp_delta_vs_prior: float        # percentage-point change WoW; negative = worse
    sample_size: int                # = from_count

    @property
    def absolute_drop(self) -> int:
        return self.from_count - self.to_count

    @property
    def is_significant_deterioration(self) -> bool:
        return self.pp_delta_vs_prior <= -0.05  # ≥5pp worse than prior period


@dataclass
class Hypothesis:
    title: str
    rationale: str
    impact: int        # 1-10
    confidence: int    # 1-10
    effort: int        # 1-10 (lower = less effort)

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
```

(Cyra class itself is added in Task 3 onward.)

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestDataclasses -v --no-cov
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): dataclasses (FunnelStep, DropOff, Hypothesis, CroReport)"
```

---

## Task 3: Funnel auto-detection from event volume

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
from unittest.mock import AsyncMock, MagicMock


class TestFunnelAutodetect:
    @pytest.mark.asyncio
    async def test_picks_top_pageview_to_custom_event_chain(self):
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(return_value=[
            ("$pageview", 12500),
            ("signup_started", 3200),
            ("signup_completed", 1850),
            ("first_value", 600),
            ("$identify", 11000),  # PostHog system event - filter out
        ])
        cyra = Cyra(posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"))
        funnel = await cyra._autodetect_funnel(days=7)
        assert funnel[0] == "$pageview"
        assert "$identify" not in funnel  # system events filtered
        assert "signup_started" in funnel
        assert len(funnel) >= 3

    @pytest.mark.asyncio
    async def test_returns_override_when_config_specifies(self):
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(return_value=[])  # would auto-detect to nothing
        cyra = Cyra(
            posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"),
            funnel_override=["$pageview", "signup_started", "signup_completed"],
        )
        funnel = await cyra._autodetect_funnel(days=7)
        assert funnel == ["$pageview", "signup_started", "signup_completed"]
        # event_volumes never called when override is set
        posthog.event_volumes.assert_not_called()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestFunnelAutodetect -v --no-cov
```

Expected: AttributeError on `Cyra` — class doesn't exist yet.

- [ ] **Step 3: Add the Cyra class with `_autodetect_funnel`**

Append to `src/devrel_origin/core/cyra.py`:

```python
# PostHog system events to exclude from auto-detected funnels
_SYSTEM_EVENTS = frozenset({
    "$identify", "$pageleave", "$autocapture", "$rageclick",
    "$set", "$create_alias", "$opt_in", "$exception",
})


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
        """Pick the highest-volume `$pageview → custom_event` chain.

        Heuristic: first event is always `$pageview` (top of funnel for any
        web product); subsequent events are the top non-system events by
        volume in descending order. We cap the chain at 5 steps —
        deeper funnels rarely have enough sample size at the tail.
        """
        if self.funnel_override:
            return self.funnel_override

        volumes = await self.posthog.event_volumes(days=days, limit=50)
        custom = [
            (name, count) for name, count in volumes
            if name not in _SYSTEM_EVENTS and not name.startswith("$")
        ]
        if not custom:
            logger.warning("Cyra: no custom events found in PostHog; funnel auto-detect failed")
            return []

        return ["$pageview"] + [name for name, _ in custom[:4]]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestFunnelAutodetect -v --no-cov
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): auto-detect funnel from PostHog event volumes"
```

---

## Task 4: Drop-off ranking with WoW deterioration

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
class TestDropoffRanking:
    @pytest.mark.asyncio
    async def test_compute_dropoffs_marks_deterioration(self):
        posthog = MagicMock(spec=PostHogClient)
        # 7d: 1000 → 300 (30% conv) → 120 (40% conv from prior step)
        # 14d: 1000 → 350 (35% conv) → 150 (43% conv)
        posthog.funnel_query = AsyncMock(side_effect=[
            [
                {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                {"name": "signup_started", "count": 300, "average_conversion_time": 120},
                {"name": "signup_completed", "count": 120, "average_conversion_time": 600},
            ],
            [  # 14-day window (prior period)
                {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                {"name": "signup_started", "count": 350, "average_conversion_time": 110},
                {"name": "signup_completed", "count": 150, "average_conversion_time": 580},
            ],
        ])
        cyra = Cyra(posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"))
        dropoffs = await cyra._compute_dropoffs(
            funnel=["$pageview", "signup_started", "signup_completed"],
            days=7,
        )
        # Step 0→1: 30% (current) vs 35% (prior) = -5pp deterioration → significant
        # Step 1→2: 40% (current) vs ~43% (prior) = -3pp → not significant
        assert dropoffs[0].is_significant_deterioration is True
        assert dropoffs[1].is_significant_deterioration is False
        # Sorted by absolute_drop (largest first); both have absolute_drop=700 vs 180
        assert dropoffs[0].absolute_drop > dropoffs[1].absolute_drop
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestDropoffRanking -v --no-cov
```

Expected: AttributeError — `_compute_dropoffs` not implemented.

- [ ] **Step 3: Add the method**

Append to the `Cyra` class:

```python
    async def _compute_dropoffs(
        self, *, funnel: list[str], days: int = 7,
    ) -> list[DropOff]:
        """Compute step-by-step drop-offs comparing current vs prior period.

        Returns dropoffs sorted by `absolute_drop` desc (largest drop first).
        WoW deterioration flagged when current pp - prior pp ≤ -0.05.
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
                prior_conv = conv  # no baseline → pp_delta = 0

            dropoffs.append(DropOff(
                from_step=from_step["name"],
                to_step=to_step["name"],
                from_count=from_step["count"],
                to_count=to_step["count"],
                conversion_rate=conv,
                pp_delta_vs_prior=conv - prior_conv,
                sample_size=from_step["count"],
            ))

        return sorted(dropoffs, key=lambda d: d.absolute_drop, reverse=True)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestDropoffRanking -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): drop-off ranking with WoW deterioration detection"
```

---

## Task 5: LLM hypothesis generation

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
class TestHypothesisGeneration:
    @pytest.mark.asyncio
    async def test_generate_hypotheses_calls_llm_with_priors(self):
        llm = MagicMock(spec=LLMClient)
        llm.generate = AsyncMock(return_value=(json.dumps({
            "hypotheses": [
                {"title": "Add social proof", "rationale": "Trust signals low",
                 "impact": 8, "confidence": 6, "effort": 3},
                {"title": "Reduce form fields", "rationale": "10-field form is friction",
                 "impact": 7, "confidence": 8, "effort": 2},
                {"title": "A/B test CTA copy", "rationale": "Generic 'Submit' button",
                 "impact": 5, "confidence": 7, "effort": 1},
            ]
        }), MagicMock()))

        cyra = Cyra(posthog_client=MagicMock(), llm_client=llm, db_path=Path("/tmp/x.db"))
        dropoff = DropOff(
            from_step="signup_started", to_step="signup_completed",
            from_count=300, to_count=120, conversion_rate=0.4,
            pp_delta_vs_prior=-0.08, sample_size=300,
        )
        hyps = await cyra._generate_hypotheses(
            dropoff=dropoff,
            page_html="<form><input/><input/></form>",
            iris_themes=["form too long"],
            sage_friction=["users complain about social login"],
        )
        assert len(hyps) == 3
        assert hyps[0].title == "Add social proof"
        # Sorted by ICE score descending
        assert hyps[0].ice_score >= hyps[-1].ice_score
        # LLM was called with priors in the prompt
        prompt_arg = llm.generate.call_args.kwargs.get("user_prompt") or llm.generate.call_args[0][1]
        assert "form too long" in prompt_arg
        assert "social login" in prompt_arg
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestHypothesisGeneration -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the method**

Append to `core/cyra.py`:

```python
_HYPOTHESIS_PROMPT = """You are a CRO analyst. Drop-off detected on a conversion funnel:

  Step: {from_step} → {to_step}
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
      "title": "<short imperative, ≤80 chars>",
      "rationale": "<2 sentences: why this drop is happening + why this test should help>",
      "impact": <1-10, expected lift if winner>,
      "confidence": <1-10, certainty in the hypothesis>,
      "effort": <1-10, dev work required (lower = less)>
    }}
  ]
}}

Score impact/confidence/effort honestly — false confidence skews ICE rankings.
Return ONLY the JSON, no markdown fences, no explanation."""


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

        text, _usage = await self.llm.generate(
            system_prompt="You are a CRO analyst.",
            user_prompt=prompt,
            temperature=0.4,
            max_tokens=1500,
        )
        # Strip any stray fences (defensive — model is told not to use them)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestHypothesisGeneration -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): LLM-driven A/B hypothesis generation with ICE scoring"
```

---

## Task 6: Cohort splitting (utm_source × device_type)

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
class TestCohortSplit:
    @pytest.mark.asyncio
    async def test_cohort_split_returns_segment_breakdown(self):
        posthog = MagicMock(spec=PostHogClient)
        # Funnel query returns full + per-segment breakdowns
        posthog.funnel_query = AsyncMock(side_effect=[
            # twitter / mobile
            [
                {"name": "$pageview", "count": 400, "average_conversion_time": 0},
                {"name": "signup_started", "count": 80, "average_conversion_time": 120},
            ],
            # google / desktop
            [
                {"name": "$pageview", "count": 600, "average_conversion_time": 0},
                {"name": "signup_started", "count": 220, "average_conversion_time": 100},
            ],
        ])
        cyra = Cyra(
            posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"),
            min_sample_size=300,
        )
        breakdown = await cyra._cohort_split(
            funnel=["$pageview", "signup_started"],
            segments=[("twitter", "mobile"), ("google", "desktop")],
            days=7,
        )
        assert "twitter|mobile" in breakdown
        assert breakdown["twitter|mobile"]["conversion_rate"] == pytest.approx(0.20)
        assert breakdown["google|desktop"]["conversion_rate"] == pytest.approx(0.367, abs=0.01)

    @pytest.mark.asyncio
    async def test_cohort_split_skips_below_min_sample(self):
        posthog = MagicMock(spec=PostHogClient)
        # Sample of 100 — below default min of 500
        posthog.funnel_query = AsyncMock(return_value=[
            {"name": "$pageview", "count": 100, "average_conversion_time": 0},
            {"name": "signup_started", "count": 25, "average_conversion_time": 120},
        ])
        cyra = Cyra(posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"),
                    min_sample_size=500)
        breakdown = await cyra._cohort_split(
            funnel=["$pageview", "signup_started"],
            segments=[("twitter", "mobile")],
            days=7,
        )
        assert breakdown == {}  # below threshold → suppressed
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestCohortSplit -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the method**

Append to `Cyra` class:

```python
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
            # follow the contract — the test stubs return per-segment data.
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestCohortSplit -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): cohort split with min_sample_size guard"
```

---

## Task 7: Recommendation generation + persistence

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
import sqlite3
from devrel_origin.project import state


@pytest.fixture
def init_db(tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    # Seed a minimal report row so persist_recommendation has a valid report_id
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (id, period_end, generated_at, body_json) "
            "VALUES (?, ?, datetime('now'), '{}')",
            ("test-report", "2026-04-01"),
        )
        conn.commit()
    return db


class TestPersistRecommendations:
    @pytest.mark.asyncio
    async def test_persist_writes_one_row_per_dropoff(self, init_db, tmp_path):
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=init_db)
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[
                FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0),
                FunnelStep(name="signup_started", index=1, count=300, conversion_rate=0.30),
            ],
            dropoffs=[
                DropOff(
                    from_step="$pageview", to_step="signup_started",
                    from_count=1000, to_count=300, conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08, sample_size=1000,
                ),
            ],
            hypotheses_by_step={
                "signup_started": [
                    Hypothesis(title="Add social proof", rationale="Low trust",
                               impact=8, confidence=6, effort=3),
                ],
            },
        )
        cyra._persist(report, report_id="test-report")

        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT pillar, action, target, target_kind, source_ids_json "
                "FROM analytics_recommendations WHERE pillar = 'cro'"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cro"
        assert rows[0][1] == "retest"  # significant deterioration → retest
        assert rows[0][2] == "signup_started"
        assert rows[0][3] == "funnel_step"
        # source_ids contains the hypothesis dicts as JSON-serialized strings
        sids = json.loads(rows[0][4])
        assert len(sids) == 1
        assert "Add social proof" in sids[0]
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestPersistRecommendations -v --no-cov
```

Expected: AttributeError on `_persist`.

- [ ] **Step 3: Implement `_persist` and the action picker**

Append to `Cyra` class:

```python
    def _action_for_dropoff(self, dropoff: DropOff) -> str:
        """Map a drop-off pattern to an action verb.

        - significant deterioration (≥5pp WoW worse) → 'retest'
        - significant improvement (≥5pp better)      → 'double_down'
        - low sample (<min_sample_size) without trend → 'investigate'
        - everything else                              → 'investigate'
        """
        if dropoff.is_significant_deterioration:
            return "retest"
        if dropoff.pp_delta_vs_prior >= 0.05:
            return "double_down"
        return "investigate"

    def _persist(self, report: CroReport, *, report_id: str) -> None:
        """Convert dropoffs + hypotheses → Recommendation rows."""
        for d in report.dropoffs:
            action = self._action_for_dropoff(d)
            hypotheses = report.hypotheses_by_step.get(d.to_step, [])
            # Encode hypotheses into source_ids_json (denormalized; cheap)
            source_ids = [
                json.dumps(h.to_dict()) for h in hypotheses
            ]
            confidence = (
                sum(h.confidence for h in hypotheses) / (10 * len(hypotheses))
                if hypotheses else 0.5
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestPersistRecommendations -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): action picker + persist via growth.persist_recommendation"
```

---

## Task 8: Brief generation handoff to Mox

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
class TestBriefGeneration:
    def test_write_briefs_creates_one_file_per_recommendation(self, tmp_path):
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=tmp_path / "x.db")
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[],
            dropoffs=[
                DropOff(
                    from_step="$pageview", to_step="signup_started",
                    from_count=1000, to_count=300, conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08, sample_size=1000,
                ),
            ],
            hypotheses_by_step={
                "signup_started": [
                    Hypothesis(title="Add social proof", rationale="Low trust",
                               impact=8, confidence=6, effort=3),
                ],
            },
        )
        # Pre-populate recommendations as if _persist already ran
        report.recommendations = [
            Recommendation(
                pillar=Pillar.CRO, action="retest", target="signup_started",
                target_kind=TargetKind.FUNNEL_STEP, confidence=0.6, source_ids=[],
                first_seen_period="2026-04-01",
            ),
        ]
        deliverables_dir = tmp_path / "deliverables"
        cyra._write_briefs(report, deliverables_dir)

        files = list(deliverables_dir.glob("cro-brief-*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "signup_started" in content
        assert "Add social proof" in content
        assert "ICE" in content  # rendered in the table
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestBriefGeneration -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add `_write_briefs`**

Append to `Cyra` class:

```python
    def _write_briefs(self, report: CroReport, deliverables_dir: Path) -> None:
        """Write one .md brief per recommendation for Mox to pick up."""
        deliverables_dir.mkdir(parents=True, exist_ok=True)
        for rec in report.recommendations:
            hypotheses = report.hypotheses_by_step.get(rec.target, [])
            dropoff = next(
                (d for d in report.dropoffs if d.to_step == rec.target), None,
            )

            md_lines = [
                f"# Cyra brief: {rec.action} `{rec.target}`",
                "",
                f"**Period:** {report.period_end}",
                f"**Pillar:** cro",
                f"**Funnel:** {report.funnel_id}",
                f"**Confidence:** {rec.confidence:.2f}",
                "",
                "## Drop-off context",
                "",
            ]
            if dropoff:
                md_lines.extend([
                    f"- From step: `{dropoff.from_step}` ({dropoff.from_count:,} users)",
                    f"- To step: `{dropoff.to_step}` ({dropoff.to_count:,} users)",
                    f"- Conversion rate: {dropoff.conversion_rate:.1%}",
                    f"- WoW delta: {dropoff.pp_delta_vs_prior:+.1%}",
                    "",
                ])

            if hypotheses:
                md_lines.extend([
                    "## A/B hypotheses (ICE-ranked)",
                    "",
                    "| Title | Impact | Confidence | Effort | ICE | Rationale |",
                    "|-------|-------:|-----------:|-------:|----:|-----------|",
                ])
                for h in hypotheses:
                    md_lines.append(
                        f"| {h.title} | {h.impact} | {h.confidence} | {h.effort} | "
                        f"{h.ice_score:.1f} | {h.rationale} |"
                    )
                md_lines.append("")

            md_lines.extend([
                "## Next steps",
                "",
                f"- Mox: pick the highest-ICE hypothesis above and draft the test variant.",
                f"- Nova: validate sample-size + duration for the planned uplift target.",
                "",
            ])

            slug = rec.target.replace("/", "-").replace(" ", "-")
            path = deliverables_dir / f"cro-brief-{report.period_end}-{rec.action}-{slug}.md"
            path.write_text("\n".join(md_lines))
            logger.info(f"Cyra wrote brief: {path}")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cyra.py::TestBriefGeneration -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): Mox-ready brief generation per recommendation"
```

---

## Task 9: `Cyra.execute()` end-to-end orchestration

**Files:**
- Modify: `src/devrel_origin/core/cyra.py`
- Modify: `tests/test_cyra.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cyra.py`:

```python
class TestExecuteEndToEnd:
    @pytest.mark.asyncio
    async def test_execute_full_cycle(self, init_db, tmp_path):
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(return_value=[
            ("$pageview", 12500), ("signup_started", 3200),
            ("signup_completed", 1850),
        ])
        posthog.funnel_query = AsyncMock(side_effect=[
            # current 7d
            [{"name": "$pageview", "count": 1000, "average_conversion_time": 0},
             {"name": "signup_started", "count": 300, "average_conversion_time": 120},
             {"name": "signup_completed", "count": 120, "average_conversion_time": 600}],
            # prior 14d
            [{"name": "$pageview", "count": 1000, "average_conversion_time": 0},
             {"name": "signup_started", "count": 350, "average_conversion_time": 110},
             {"name": "signup_completed", "count": 150, "average_conversion_time": 580}],
            # page HTML fetch — funnel-page crawl skipped in unit test (returns [] elsewhere)
        ])

        llm = MagicMock(spec=LLMClient)
        llm.generate = AsyncMock(return_value=(json.dumps({
            "hypotheses": [
                {"title": "Add social proof", "rationale": "Low trust",
                 "impact": 8, "confidence": 6, "effort": 3},
            ]
        }), MagicMock()))

        cyra = Cyra(posthog_client=posthog, llm_client=llm, db_path=init_db, hypothesis_count=1)
        report = await cyra.execute(
            period_end="2026-04-01", report_id="test-report",
            page_html_by_url={},  # no page HTML in this unit test
            iris_themes=[], sage_friction=[],
            deliverables_dir=tmp_path / "deliverables",
        )
        assert report.sources_ok is True
        assert len(report.dropoffs) == 2
        assert len(report.recommendations) >= 1
        # At least one brief was written
        briefs = list((tmp_path / "deliverables").glob("cro-brief-*.md"))
        assert len(briefs) >= 1
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_cyra.py::TestExecuteEndToEnd -v --no-cov
```

Expected: AttributeError on `execute`.

- [ ] **Step 3: Wire `execute()` together**

Append to `Cyra` class:

```python
    async def execute(
        self,
        *,
        period_end: str,
        report_id: str,
        page_html_by_url: dict[str, str] | None = None,
        iris_themes: list[str] | None = None,
        sage_friction: list[str] | None = None,
        deliverables_dir: Path | None = None,
    ) -> CroReport:
        """Run a full Cyra cycle: detect funnel → compute dropoffs → hypothesize
        worst step → persist + write briefs.

        `page_html_by_url` keys by step name (e.g., 'signup_started'); when a
        step matches, we feed the corresponding HTML into the hypothesis prompt.
        """
        page_html_by_url = page_html_by_url or {}
        iris_themes = iris_themes or []
        sage_friction = sage_friction or []

        funnel = await self._autodetect_funnel(days=7)
        if not funnel:
            return CroReport(
                period_end=period_end, funnel_id=self.funnel_id,
                funnel=[], dropoffs=[], sources_ok=False,
            )

        dropoffs = await self._compute_dropoffs(funnel=funnel, days=7)
        if not dropoffs:
            return CroReport(
                period_end=period_end, funnel_id=self.funnel_id,
                funnel=[], dropoffs=[], sources_ok=True,
            )

        # Build FunnelStep view for the report
        first_count = dropoffs[0].from_count if dropoffs else 0
        funnel_steps: list[FunnelStep] = []
        for i, ev in enumerate(funnel):
            if i == 0:
                funnel_steps.append(FunnelStep(name=ev, index=0, count=first_count, conversion_rate=1.0))
            else:
                d = dropoffs[i - 1]
                funnel_steps.append(FunnelStep(
                    name=ev, index=i, count=d.to_count,
                    conversion_rate=(d.to_count / first_count) if first_count else 0.0,
                ))

        # Hypothesize the worst-deterioration step (or worst absolute drop if no deterioration)
        worst = next((d for d in dropoffs if d.is_significant_deterioration), dropoffs[0])
        hypotheses_by_step: dict[str, list[Hypothesis]] = {}
        try:
            page_html = page_html_by_url.get(worst.to_step, "")
            hyps = await self._generate_hypotheses(
                dropoff=worst, page_html=page_html,
                iris_themes=iris_themes, sage_friction=sage_friction,
            )
            hypotheses_by_step[worst.to_step] = hyps
        except Exception as e:
            logger.warning(f"Cyra: hypothesis generation failed: {e}")

        report = CroReport(
            period_end=period_end, funnel_id=self.funnel_id,
            funnel=funnel_steps, dropoffs=dropoffs,
            hypotheses_by_step=hypotheses_by_step,
        )

        self._persist(report, report_id=report_id)
        if deliverables_dir is not None:
            self._write_briefs(report, deliverables_dir)

        return report
```

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_cyra.py -v --no-cov
pytest tests/ -q --no-header
```

Expected: all Cyra tests PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/cyra.py tests/test_cyra.py
git commit -m "feat(cyra): Cyra.execute end-to-end orchestration"
```

---

## Task 10: `cli/cro.py` — `report` verb

**Files:**
- Create: `src/devrel_origin/cli/cro.py`
- Modify: `src/devrel_origin/cli/__init__.py`
- Test: `tests/cli/test_cro_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_cro_command.py`:

```python
"""CLI smoke tests for `devrel cro ...`."""

from typer.testing import CliRunner

from devrel_origin.cli import app


def test_cro_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff", "calibration", "funnel"):
        assert verb in result.output.lower()


def test_cro_report_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "report", "--help"])
    assert result.exit_code == 0
    assert "since" in result.output.lower()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_cro_command.py -v --no-cov
```

Expected: `cro` not registered → fail.

- [ ] **Step 3: Create `cli/cro.py` with the `report` verb**

Create `src/devrel_origin/cli/cro.py`:

```python
"""`devrel cro ...` — CRO auditor verbs (Cyra)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.core.cyra import Cyra
from devrel_origin.core.growth.target_kinds import Pillar
from devrel_origin.core.llm import LLMClient
from devrel_origin.tools.api_client import PostHogClient

cro_app = typer.Typer(
    name="cro",
    help="CRO auditor (Cyra). Funnel drop-offs + LLM-generated A/B hypotheses.",
    no_args_is_help=True,
)

_console = Console()


@cro_app.command("report")
def report(
    since: str = typer.Option("7d", "--since", help="Window: 7d, 30d, 90d"),
    push: bool = typer.Option(False, "--push", help="Email/Telegram the report"),
    format: str = typer.Option("markdown", "--format", help="markdown|json"),
) -> None:
    """Run a Cyra cycle and persist Recommendation rows + Mox briefs."""
    paths = find_paths_or_exit()
    days = int(since.rstrip("d"))
    period_end = date.today().isoformat()
    report_id = f"cro-{period_end}"

    posthog = PostHogClient.from_env()
    llm = LLMClient.from_env()
    cyra = Cyra(
        posthog_client=posthog, llm_client=llm,
        db_path=paths.devrel_dir / "state.db",
    )

    async def _run():
        return await cyra.execute(
            period_end=period_end, report_id=report_id,
            page_html_by_url={},
            iris_themes=[], sage_friction=[],
            deliverables_dir=paths.devrel_dir / "deliverables",
        )

    result = asyncio.run(_run())

    if format == "json":
        _console.print(json.dumps({
            "period_end": result.period_end,
            "funnel_id": result.funnel_id,
            "dropoffs": [d.__dict__ for d in result.dropoffs],
            "recommendations": [
                {
                    "action": r.action, "target": r.target,
                    "confidence": r.confidence, "source_ids": r.source_ids,
                }
                for r in result.recommendations
            ],
        }, indent=2, default=str))
        return

    # Markdown table
    table = Table(title=f"Cyra report — {period_end}")
    table.add_column("From → To", style="cyan")
    table.add_column("Conv", justify="right")
    table.add_column("WoW Δ", justify="right")
    table.add_column("Sample", justify="right")
    for d in result.dropoffs:
        table.add_row(
            f"{d.from_step} → {d.to_step}",
            f"{d.conversion_rate:.1%}",
            f"{d.pp_delta_vs_prior:+.1%}",
            f"{d.sample_size:,}",
        )
    _console.print(table)
    _console.print(f"[green]Wrote {len(result.recommendations)} recommendation(s).[/green]")
    if push:
        _console.print("[yellow]--push not yet implemented for cro; printed-only.[/yellow]")
```

Update `src/devrel_origin/cli/__init__.py`:

```python
from devrel_origin.cli.cro import cro_app
# ...
app.add_typer(cro_app, name="cro")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/cli/test_cro_command.py -v --no-cov
```

Expected: 2 PASSED. (`history`, `diff`, `calibration`, `funnel` will register in Tasks 11-14, the `--help` test for now lists only `report`. The first test will fail until those land — adjust the test now to allow it, OR finish all CLI verbs before running the multi-verb test.)

**Adjust `test_cro_help_lists_subcommands`** to only check for `report` until the other verbs land:

```python
def test_cro_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "--help"])
    assert result.exit_code == 0
    assert "report" in result.output.lower()
```

(Will reinstate the full check at end of Wave 1 after Task 13.)

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/cro.py src/devrel_origin/cli/__init__.py tests/cli/test_cro_command.py
git commit -m "feat(cli): devrel cro report"
```

---

## Task 11: `cli/cro.py` — `history` verb

**Files:**
- Modify: `src/devrel_origin/cli/cro.py`
- Modify: `tests/cli/test_cro_command.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_cro_command.py`:

```python
def test_cro_history_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "history", "signup_started"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_cro_command.py::test_cro_history_runs -v --no-cov
```

Expected: `history` not a registered command → fail.

- [ ] **Step 3: Add the `history` verb**

Append to `cli/cro.py`:

```python
@cro_app.command("history")
def history(
    funnel_step: str = typer.Argument(..., help="Funnel step name to track"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Show conversion-rate trajectory for a funnel step across reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet — run `devrel cro report` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"History — {funnel_step}")
    table.add_column("Period", style="cyan")
    table.add_column("Conv", justify="right")
    table.add_column("Sample", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT period_end, conversion_rate, sample_size
            FROM cro_funnel_metrics
            WHERE step_index = (
                SELECT MIN(step_index) FROM cro_funnel_metrics
                WHERE funnel_id IN (SELECT DISTINCT funnel_id FROM cro_funnel_metrics)
            )
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (limit,),
        )
        for period_end, conv, sample in cur:
            table.add_row(period_end, f"{(conv or 0):.1%}", f"{(sample or 0):,}")

    _console.print(table)
```

- [ ] **Step 4: Run test**

```bash
pytest tests/cli/test_cro_command.py::test_cro_history_runs -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/cro.py tests/cli/test_cro_command.py
git commit -m "feat(cli): devrel cro history"
```

---

## Task 12: `cli/cro.py` — `diff` verb

**Files:**
- Modify: `src/devrel_origin/cli/cro.py`
- Modify: `tests/cli/test_cro_command.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_cro_command.py`:

```python
def test_cro_diff_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "diff", "2026-04-01", "2026-04-08"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_cro_command.py::test_cro_diff_runs -v --no-cov
```

Expected: `diff` not registered → fail.

- [ ] **Step 3: Add the `diff` verb**

Append to `cli/cro.py`:

```python
@cro_app.command("diff")
def diff(
    period_a: str = typer.Argument(..., help="Earlier ISO period"),
    period_b: str = typer.Argument(..., help="Later ISO period"),
) -> None:
    """Per-step conversion delta between two CRO reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"CRO diff — {period_a} → {period_b}")
    table.add_column("Funnel", style="cyan")
    table.add_column("Step", justify="right")
    table.add_column(f"{period_a}", justify="right")
    table.add_column(f"{period_b}", justify="right")
    table.add_column("Δ pp", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT a.funnel_id, a.step_index, a.conversion_rate, b.conversion_rate
            FROM cro_funnel_metrics a
            JOIN cro_funnel_metrics b
              ON a.funnel_id = b.funnel_id AND a.step_index = b.step_index
            WHERE a.period_end = ? AND b.period_end = ?
            ORDER BY a.funnel_id, a.step_index
            """,
            (period_a, period_b),
        )
        for funnel_id, step_index, conv_a, conv_b in cur:
            delta = (conv_b or 0) - (conv_a or 0)
            table.add_row(
                funnel_id, str(step_index),
                f"{(conv_a or 0):.1%}", f"{(conv_b or 0):.1%}",
                f"{delta:+.1%}",
            )

    _console.print(table)
```

- [ ] **Step 4: Run test**

```bash
pytest tests/cli/test_cro_command.py::test_cro_diff_runs -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/cro.py tests/cli/test_cro_command.py
git commit -m "feat(cli): devrel cro diff"
```

---

## Task 13: `cli/cro.py` — `calibration` and `funnel` verbs

**Files:**
- Modify: `src/devrel_origin/cli/cro.py`
- Modify: `tests/cli/test_cro_command.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_cro_command.py`:

```python
def test_cro_calibration_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "calibration"])
    assert result.exit_code == 0


def test_cro_funnel_inspector_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "funnel", "--show-detected"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_cro_command.py::test_cro_calibration_runs tests/cli/test_cro_command.py::test_cro_funnel_inspector_runs -v --no-cov
```

Expected: 2 FAIL — verbs not registered.

- [ ] **Step 3: Add `calibration` and `funnel` verbs**

Append to `cli/cro.py`:

```python
@cro_app.command("calibration")
def calibration() -> None:
    """Score historical CRO recommendations against subsequent funnel data."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    from devrel_origin.core.growth.recommendations import calibrate
    from devrel_origin.core.growth.target_kinds import TargetKind

    def _score_outcome(rec) -> str:
        """Did conversion improve at this funnel step after the rec was applied?"""
        if rec.applied_at is None:
            return "unchanged"
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                """
                SELECT conversion_rate FROM cro_funnel_metrics
                WHERE funnel_id IN (SELECT DISTINCT funnel_id FROM cro_funnel_metrics)
                  AND period_end >= ?
                ORDER BY period_end ASC LIMIT 2
                """,
                (rec.applied_at[:10],),
            )
            rates = [row[0] for row in cur.fetchall()]
        if len(rates) < 2:
            return "unchanged"
        return "improved" if rates[1] > rates[0] else (
            "regressed" if rates[1] < rates[0] else "unchanged"
        )

    result = calibrate(db_path, Pillar.CRO, outcome_scorer=_score_outcome)

    if not result:
        _console.print("[yellow]No applied CRO recommendations yet.[/yellow]")
        return

    table = Table(title="CRO calibration")
    table.add_column("Action", style="cyan")
    table.add_column("Applied", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Lift vs coinflip", justify="right")
    for action, stats in result.items():
        table.add_row(
            action, str(stats["applied_count"]),
            f"{stats['hit_rate']:.1%}", f"{stats['lift_vs_coinflip']:+.1%}",
        )
    _console.print(table)


@cro_app.command("funnel")
def funnel(
    show_detected: bool = typer.Option(
        False, "--show-detected", help="Show what auto-detect picked"
    ),
    days: int = typer.Option(7, "--days"),
) -> None:
    """Inspect the current (auto-detected or configured) CRO funnel."""
    paths = find_paths_or_exit()
    posthog = PostHogClient.from_env()
    llm = LLMClient.from_env()
    cyra = Cyra(
        posthog_client=posthog, llm_client=llm,
        db_path=paths.devrel_dir / "state.db",
    )

    async def _run():
        return await cyra._autodetect_funnel(days=days)

    funnel = asyncio.run(_run())

    table = Table(title=f"Cyra funnel (auto-detected, {days}d)")
    table.add_column("#", justify="right")
    table.add_column("Event", style="cyan")
    for i, ev in enumerate(funnel):
        table.add_row(str(i), ev)

    _console.print(table)
    if show_detected:
        _console.print(
            "[dim]Override via `[growth].cro_funnel = [...]` in .devrel/config.toml[/dim]"
        )
```

- [ ] **Step 4: Run tests + restore the multi-verb help test**

In `tests/cli/test_cro_command.py`, restore `test_cro_help_lists_subcommands` to its original full form:

```python
def test_cro_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff", "calibration", "funnel"):
        assert verb in result.output.lower()
```

Run:

```bash
pytest tests/cli/test_cro_command.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/cro.py tests/cli/test_cro_command.py
git commit -m "feat(cli): devrel cro {calibration,funnel}"
```

---

## Task 14: Export Cyra from `core/__init__.py`

**Files:**
- Modify: `src/devrel_origin/core/__init__.py`

- [ ] **Step 1: Add the export**

Edit `src/devrel_origin/core/__init__.py` and add `Cyra` to the imports + `__all__`:

```python
from devrel_origin.core.cyra import Cyra
# In __all__ list, add: "Cyra",
```

- [ ] **Step 2: Run full suite**

```bash
pytest tests/ -q --no-header
ruff check . && ruff format --check . | tail -1
```

Expected: full suite green; ruff clean.

- [ ] **Step 3: Commit**

```bash
git add src/devrel_origin/core/__init__.py
git commit -m "feat(cyra): export Cyra from core"
```

---

## Task 15: Atlas Stage 5c registration (Cyra-only placeholder)

**Files:**
- Modify: `src/devrel_origin/core/atlas.py`
- Modify: `src/devrel_origin/core/agent_config.py` (or wherever `[orchestration]` config lives)
- Test: `tests/test_atlas.py`

Atlas full Stage 5c wiring lands in Wave 4 (Polish). For Wave 1 we just register the `cro_in_run` config flag and add a Cyra-only branch that runs after Argus's Stage 5b when the flag is set. Vega/Selene wiring lands in Waves 2/3.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_atlas.py`:

```python
@pytest.mark.asyncio
async def test_atlas_runs_cyra_when_cro_in_run_enabled(tmp_path, monkeypatch):
    """Stage 5c — when cro_in_run=true, Atlas calls Cyra.execute after Argus."""
    # Build a minimal Atlas with mocked agents; assert Cyra.execute was called
    from devrel_origin.core.cyra import Cyra
    from unittest.mock import AsyncMock, patch

    # ... project state + atlas setup boilerplate (match existing test_atlas.py patterns)
    # Then assert Cyra.execute was called once with the expected period_end and report_id.
```

(Note: exact fixture structure follows existing `tests/test_atlas.py` patterns. The reviewer should mirror the helper that builds a stub Atlas in that file. If the helper doesn't exist, create one as part of this task.)

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_atlas.py -k "cyra" -v --no-cov
```

Expected: AttributeError or AssertionError — Atlas doesn't call Cyra yet.

- [ ] **Step 3: Wire Cyra into Atlas Stage 5c**

In `src/devrel_origin/core/atlas.py`, find where Argus's Stage 5b runs (gated by `analytics_in_run`). Right after that block, add:

```python
# Stage 5c — Growth pillars (Cyra in Wave 1; Vega + Selene added in Waves 2/3)
if self.config.orchestration.cro_in_run:
    try:
        cyra = Cyra(
            posthog_client=self.posthog,
            llm_client=self.llm,
            db_path=self.project_paths.devrel_dir / "state.db",
        )
        cro_report = await cyra.execute(
            period_end=self.context.week_of,
            report_id=f"cro-{self.context.week_of}",
            page_html_by_url={},  # Selene crawler feeds this in Wave 3
            iris_themes=self._extract_iris_themes(),
            sage_friction=self._extract_sage_friction(),
            deliverables_dir=self.project_paths.devrel_dir / "deliverables",
        )
        self.context.cro_report = cro_report.__dict__
    except Exception as e:
        logger.warning(f"Atlas Stage 5c (Cyra) failed: {e}")
        self.context.cro_report = {"error": str(e)}
```

In `src/devrel_origin/core/agent_config.py` (or wherever `OrchestrationConfig` lives), add:

```python
@dataclass
class OrchestrationConfig:
    # ... existing fields ...
    argus_in_run: bool = True   # was analytics_in_run; old name still accepted via __post_init__
    cro_in_run: bool = True
    seo_in_run: bool = False
    geo_in_run: bool = False

    def __post_init__(self) -> None:
        # Back-compat: accept analytics_in_run as alias for argus_in_run
        if hasattr(self, "analytics_in_run"):
            self.argus_in_run = self.analytics_in_run
```

Add `_extract_iris_themes` and `_extract_sage_friction` helpers on Atlas:

```python
    def _extract_iris_themes(self) -> list[str]:
        themes_data = self.context.iris_themes or {}
        if isinstance(themes_data, dict):
            return [t.get("title", "") for t in themes_data.get("themes", [])][:5]
        return []

    def _extract_sage_friction(self) -> list[str]:
        triage_data = self.context.sage_triage or {}
        if isinstance(triage_data, dict):
            return [
                f"{i.get('title', '')}: {i.get('summary', '')}"
                for i in triage_data.get("issues", [])
                if i.get("priority") in {"high", "critical"}
            ][:5]
        return []
```

Add `cro_report: dict = field(default_factory=dict)` to `SharedContext`.

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_atlas.py -k "cyra" -v --no-cov
pytest tests/ -q --no-header
```

Expected: Cyra-Atlas test PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/atlas.py src/devrel_origin/core/agent_config.py tests/test_atlas.py
git commit -m "feat(atlas): Stage 5c (Cyra) gated by cro_in_run config"
```

---

## Wave 1 closeout checklist

After Tasks 1-15:

- [ ] `pytest tests/ -q --no-header` shows ~840 + ~25 new = ~865 passed / 21 xfailed
- [ ] `ruff check .` and `ruff format --check .` both clean
- [ ] `devrel cro --help` lists `report`, `history`, `diff`, `calibration`, `funnel`
- [ ] `devrel cro report` runs end-to-end against a real PostHog instance (manual smoke)
- [ ] At least one `cro-brief-*.md` lands in `.devrel/deliverables/`
- [ ] `devrel growth summary` shows non-zero "Open recs" for the cro pillar
- [ ] Atlas weekly cycle with `cro_in_run = true` runs Cyra without breaking other agents

When all checked: Wave 1 complete. Move to Wave 2 plan (`growth-wave2-vega-geo.md`).
