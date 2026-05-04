"""
Iris — Feedback Synthesizer Agent

Extracts themes from developer feedback across GitHub, Discourse, and support
channels. Ranks pain points and maps the developer journey.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.base import strip_markdown_fences
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)

# Max signals to send in a single LLM call to avoid oversized/truncated responses
_MAX_SIGNALS_PER_CALL = 30

# Jaccard similarity threshold for merging near-duplicate themes.
# Calibrated for theme titles in the 4-8 word range (typical LLM output).
# Two themes whose normalized title token sets share >= 50% are merged.
# Lower this if you see near-duplicate themes proliferating; raise it if
# distinct themes are being incorrectly merged.
SIMILARITY_THRESHOLD = 0.5


def _safe_json_loads(text: str) -> dict:
    """Parse JSON from LLM output with regex fallback for malformed responses."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find the outermost JSON object via brace matching
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break

    raise json.JSONDecodeError("Could not extract valid JSON", text, 0)


@dataclass
class FeedbackTheme:
    """A recurring theme extracted from developer feedback."""

    theme_id: str
    title: str
    description: str
    frequency: int  # Number of mentions
    severity: float  # 1-10 scale
    composite_score: float  # frequency * severity
    sources: list[str]  # Where this theme appeared
    representative_quotes: list[str]
    product_areas: list[str]
    recommended_actions: list[str]


@dataclass
class DeveloperJourneyStage:
    """Pain points mapped to a stage in the developer journey."""

    stage: str  # discovery, evaluation, onboarding, integration, scaling
    pain_points: list[str]
    friction_score: float  # 1-10
    drop_off_risk: str  # low, medium, high


@dataclass
class FeedbackSynthesis:
    """Complete feedback synthesis report."""

    period: str
    total_signals: int
    themes: list[FeedbackTheme]
    journey_map: list[DeveloperJourneyStage]
    product_recommendations: list[dict[str, str]]
    content_opportunities: list[str]


class Iris:
    """
    Feedback Synthesizer agent for cross-channel developer insight extraction.

    Capabilities:
    - Extract recurring themes from GitHub issues, Discourse, and support
    - Rank pain points by frequency x severity composite score
    - Map pain points to developer journey stages
    - Generate product recommendations backed by evidence
    - Identify content opportunities (tutorials that would address top pain points)

    Tools:
    1. github_issues_analyzer — Batch-analyze issue titles/bodies for themes
    2. discourse_fetcher — Pull recent Discourse posts and replies
    3. support_ticket_reader — Read support channel messages
    4. theme_extractor — NLP-based theme clustering
    5. sentiment_aggregator — Aggregate sentiment across sources
    6. pain_point_ranker — Score pain points by frequency x severity
    7. journey_mapper — Map pain points to developer journey stages
    8. quote_selector — Select representative quotes for each theme
    9. product_recommender — Generate evidence-backed product recommendations
    10. content_gap_finder — Identify tutorials/docs that would address pain points
    11. trend_detector — Compare themes week-over-week for emerging issues
    12. report_compiler — Generate the final synthesis document
    """

    SYSTEM_PROMPT = """You are Iris, a feedback synthesizer for OpenClaw. You analyze
developer feedback from multiple channels to extract actionable insights.

Your synthesis principles:
1. EVIDENCE-BASED — Every theme must be backed by specific quotes and counts
2. ACTIONABLE — Don't just describe problems, recommend solutions
3. JOURNEY-AWARE — Map pain points to where developers are in their journey
4. PRIORITIZED — Rank by composite score (frequency x severity), not just volume
5. CROSS-CHANNEL — Same theme in GitHub AND Discourse is stronger than either alone

Developer journey stages for OpenClaw:
1. Discovery — Finding the agent system, comparing to manual DevRel or competitors (Orbit, Common Room)
2. Evaluation — Cloning the repo, reading docs, running a single agent
3. Onboarding — Configuring knowledge base, connecting APIs, running first weekly cycle
4. Integration — Customizing agent prompts, adding MCP tools, tuning scoring/eval
5. Scaling — Team rollout, multi-product deployment, advanced orchestration configurations

Pain point severity scale:
- 1-3: Minor friction (confusing docs, UI annoyance)
- 4-6: Moderate blocker (feature gap, integration difficulty)
- 7-9: Major blocker (data loss risk, performance at scale)
- 10: Critical (security issue, complete failure)"""

    JOURNEY_KEYWORDS: dict[str, list[str]] = {
        "discovery": ["comparison", "alternative", "vs", "evaluate"],
        "evaluation": ["docs", "documentation", "tutorial", "example", "trial"],
        "onboarding": ["install", "setup", "init", "gateway", "first message", "getting started"],
        "integration": ["skill", "plugin", "voice", "channel", "provider", "llm"],
        "scaling": ["scale", "performance", "self-host", "team", "multi-device"],
    }

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a feedback synthesis task.

        Pulls from Sage's triage data and additional sources to
        produce a ranked, evidence-backed synthesis.
        """
        logger.info(f"Iris executing: {task[:80]}...")

        sage_issues = []
        if context and "sage_triage" in context:
            sage_data = context["sage_triage"]
            if isinstance(sage_data, dict):
                sage_issues = sage_data.get("issues", [])

        themes = await self._extract_themes(sage_issues)
        journey_map = self._map_to_journey(themes)
        recommendations = self._generate_recommendations(themes)
        content_gaps = self._find_content_opportunities(themes)

        return {
            "agent": "iris",
            "task": task,
            "themes": [
                {
                    "theme_id": t.theme_id,
                    "title": t.title,
                    "description": t.description,
                    "frequency": t.frequency,
                    "severity": t.severity,
                    "composite_score": t.composite_score,
                    "sources": t.sources,
                    "product_areas": t.product_areas,
                    "recommended_actions": t.recommended_actions,
                }
                for t in themes
            ],
            "journey_map": {
                stage.stage: {
                    "friction_score": stage.friction_score,
                    "pain_points": stage.pain_points,
                    "drop_off_risk": stage.drop_off_risk,
                }
                for stage in journey_map
            },
            "product_recommendations": recommendations,
            "content_opportunities": content_gaps,
            "upstream_issues_processed": len(sage_issues),
            "status": "synthesized",
        }

    async def synthesize_weekly(
        self,
        sage_triage: dict[str, Any],
        discourse_posts: Optional[list[dict]] = None,
        support_tickets: Optional[list[dict]] = None,
    ) -> FeedbackSynthesis:
        """Run a full weekly feedback synthesis."""
        all_signals = []

        # Ingest from all sources
        if sage_triage.get("issues"):
            all_signals.extend(sage_triage["issues"])
        if discourse_posts:
            all_signals.extend(discourse_posts)
        if support_tickets:
            all_signals.extend(support_tickets)

        # Extract and rank themes
        themes = await self._extract_themes(all_signals)
        journey_map = self._map_to_journey(themes)
        recommendations = self._generate_recommendations(themes)
        content_gaps = self._find_content_opportunities(themes)

        return FeedbackSynthesis(
            period="weekly",
            total_signals=len(all_signals),
            themes=themes,
            journey_map=journey_map,
            product_recommendations=recommendations,
            content_opportunities=content_gaps,
        )

    async def _extract_themes(self, signals: list[dict]) -> list[FeedbackTheme]:
        """Extract recurring themes from all feedback signals via LLM.

        Processes signals in chunks of _MAX_SIGNALS_PER_CALL, extracts
        themes from each chunk, then merges overlapping themes by title
        similarity. This ensures no signals are silently dropped.
        """
        # Distinguish the two early-return paths in logs so "no themes
        # this week" can be diagnosed without re-running the agent.
        if not signals:
            logger.info("Iris._extract_themes: no signals provided; returning empty themes list")
            return []
        if not self.llm_client:
            logger.warning("Iris._extract_themes: no LLM client available; cannot extract themes")
            return []

        # Process in chunks
        all_themes: list[FeedbackTheme] = []
        for i in range(0, len(signals), _MAX_SIGNALS_PER_CALL):
            chunk = signals[i : i + _MAX_SIGNALS_PER_CALL]
            chunk_themes = await self._extract_themes_from_chunk(chunk)
            all_themes.extend(chunk_themes)

        if len(signals) > _MAX_SIGNALS_PER_CALL:
            logger.info(
                "Processed %d signals in %d chunks, got %d raw themes",
                len(signals),
                (len(signals) + _MAX_SIGNALS_PER_CALL - 1) // _MAX_SIGNALS_PER_CALL,
                len(all_themes),
            )

        # Merge themes with the same or similar titles
        merged = self._merge_themes(all_themes)
        return sorted(merged, key=lambda t: t.composite_score, reverse=True)

    async def _extract_themes_from_chunk(
        self,
        signals: list[dict],
    ) -> list[FeedbackTheme]:
        """Extract themes from a single chunk of signals."""
        issues_text = "\n".join(
            f"- #{s.get('number', '?')}: {s.get('title', '')} — {s.get('category', 'unknown')}"
            for s in signals
        )

        prompt = f"""Analyze these developer feedback signals and extract recurring themes.

Signals:
{issues_text}

Return a JSON object with a "themes" array. Each theme has:
- theme_id: short unique string
- title: concise theme name
- description: 1-2 sentence explanation
- frequency: how many signals relate to this theme (integer)
- severity: 1-10 severity score (float)
- sources: list of platforms observed in the signals you classified (typically a subset of github, discourse, twitter, support_tickets — infer from the signals above)
- representative_issues: list of issue numbers (e.g. ["#123", "#456"]) from the signals above
- product_areas: which areas are affected (orchestration, agent SDK, MCP tools, knowledge base, scoring/eval, prompt optimization, onboarding/docs, security)
- recommended_actions: 1-2 concrete actions to address this

Return ONLY valid JSON, no markdown fences."""

        try:
            raw = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=2048,
                model="haiku",
            )
            raw = strip_markdown_fences(raw)
            data = _safe_json_loads(raw)
            themes = []
            for t in data.get("themes", []):
                freq = t.get("frequency", 1)
                sev = t.get("severity", 5.0)
                themes.append(
                    FeedbackTheme(
                        theme_id=t.get("theme_id", ""),
                        title=t.get("title", ""),
                        description=t.get("description", ""),
                        frequency=freq,
                        severity=sev,
                        composite_score=freq * sev,
                        sources=t.get("sources", []),
                        representative_quotes=t.get(
                            "representative_issues", t.get("representative_quotes", [])
                        ),
                        product_areas=t.get("product_areas", []),
                        recommended_actions=t.get("recommended_actions", []),
                    )
                )
            return themes
        except Exception as exc:
            logger.warning(f"Theme extraction failed for chunk: {exc}")
            return []

    @staticmethod
    def _token_jaccard(a: str, b: str) -> float:
        """Compute Jaccard similarity between two title strings."""
        tokens_a = set(a.lower().split())
        tokens_b = set(b.lower().split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    @classmethod
    def _merge_themes(cls, themes: list[FeedbackTheme]) -> list[FeedbackTheme]:
        """Merge themes with similar titles using fuzzy matching.

        Uses token-overlap Jaccard similarity (threshold from module-level
        ``SIMILARITY_THRESHOLD``) to group themes that the LLM named
        differently across chunks. When themes merge, frequencies are
        summed and severity is averaged.
        """
        # Build groups using greedy fuzzy matching
        groups: list[list[FeedbackTheme]] = []
        for t in themes:
            placed = False
            for group in groups:
                # Check against the group representative (first element)
                if cls._token_jaccard(t.title, group[0].title) >= SIMILARITY_THRESHOLD:
                    group.append(t)
                    placed = True
                    break
            if not placed:
                groups.append([t])

        merged: list[FeedbackTheme] = []
        for group in groups:
            if len(group) == 1:
                merged.append(group[0])
                continue
            # Combine
            total_freq = sum(t.frequency for t in group)
            avg_sev = sum(t.severity for t in group) / len(group)
            all_quotes = []
            all_areas = set()
            all_actions = []
            all_sources = set()
            for t in group:
                all_quotes.extend(t.representative_quotes)
                all_areas.update(t.product_areas)
                all_actions.extend(t.recommended_actions)
                all_sources.update(t.sources)

            merged.append(
                FeedbackTheme(
                    theme_id=group[0].theme_id,
                    title=group[0].title,
                    description=group[0].description,
                    frequency=total_freq,
                    severity=round(avg_sev, 1),
                    composite_score=round(total_freq * avg_sev, 1),
                    sources=list(all_sources),
                    representative_quotes=all_quotes[:10],
                    product_areas=list(all_areas),
                    recommended_actions=list(dict.fromkeys(all_actions))[:3],
                )
            )

        return merged

    def _map_to_journey(self, themes: list[FeedbackTheme]) -> list[DeveloperJourneyStage]:
        """Map themes to developer journey stages based on product areas and keywords."""
        # Include an explicit "other" bucket. Defaulting unmatched themes to
        # "onboarding" systematically inflates onboarding-friction signal
        # for mature products, where most themes are about scaling and
        # integration. "other" is honest about not knowing.
        stage_data: dict[str, list[FeedbackTheme]] = {stage: [] for stage in self.JOURNEY_KEYWORDS}
        stage_data["other"] = []

        for theme in themes:
            text = f"{theme.title} {theme.description} {' '.join(theme.product_areas)}".lower()
            matched = False
            for stage, keywords in self.JOURNEY_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    stage_data[stage].append(theme)
                    matched = True
                    break
            if not matched:
                stage_data["other"].append(theme)

        # One summary log per call rather than per theme — keeps signal
        # actionable without flooding logs on a 50-theme run.
        unmatched = stage_data["other"]
        if unmatched:
            logger.info(
                "%d theme(s) routed to 'other' journey stage: %s",
                len(unmatched),
                [t.title for t in unmatched],
            )

        result = []
        for stage, matched_themes in stage_data.items():
            if matched_themes:
                avg_severity = sum(t.severity for t in matched_themes) / len(matched_themes)
                risk = "high" if avg_severity >= 7 else "medium" if avg_severity >= 4 else "low"
            else:
                avg_severity = 0.0
                risk = "low"

            result.append(
                DeveloperJourneyStage(
                    stage=stage,
                    pain_points=[t.title for t in matched_themes],
                    friction_score=round(avg_severity, 1),
                    drop_off_risk=risk,
                )
            )

        return result

    def _generate_recommendations(self, themes: list[FeedbackTheme]) -> list[dict[str, str]]:
        """Generate product recommendations from themes."""
        return [
            {
                "theme": theme.title,
                "recommendation": action,
                "evidence": f"{theme.frequency} mentions, severity {theme.severity}/10",
            }
            for theme in themes
            for action in theme.recommended_actions[:1]
        ]

    def _find_content_opportunities(self, themes: list[FeedbackTheme]) -> list[str]:
        """Build content briefs from themes — title + top recommended action.

        Each brief is a short string Kai can use as a writing prompt without
        further synthesis. When a theme has no recommended_actions, the
        fallback surfaces severity and frequency so Kai's KB-search has
        enough context to find related material.
        """
        ranked = sorted(themes, key=lambda t: t.composite_score, reverse=True)[:5]
        opportunities: list[str] = []
        for theme in ranked:
            actions = getattr(theme, "recommended_actions", None) or []
            top_action = actions[0] if actions else None
            if top_action:
                opportunities.append(f"Tutorial on '{theme.title}': {top_action}")
            else:
                opportunities.append(
                    f"Tutorial on '{theme.title}' "
                    f"(severity={theme.severity}, freq={theme.frequency})"
                )
        return opportunities
