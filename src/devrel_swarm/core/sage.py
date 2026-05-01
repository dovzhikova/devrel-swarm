"""
Sage — Community Manager Agent

Triages GitHub issues, analyzes sentiment, flags at-risk contributors,
and identifies community champions.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.github_tools import GitHubIssue, GitHubTools

logger = logging.getLogger(__name__)


class IssuePriority(Enum):
    CRITICAL = "critical"  # Data loss, security, complete breakage
    HIGH = "high"  # Feature broken, no workaround
    MEDIUM = "medium"  # Feature degraded, workaround exists
    LOW = "low"  # Enhancement, cosmetic, nice-to-have


class SentimentScore(Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    CHURNING = "churning"  # Signals intent to leave


@dataclass
class TriagedIssue:
    """A GitHub issue that has been triaged and categorized."""

    issue_number: int
    title: str
    author: str
    priority: IssuePriority
    sentiment: SentimentScore
    category: str  # bug, feature_request, question, docs, performance
    product_area: str  # analytics, replay, flags, experiments, surveys, etc.
    summary: str
    suggested_response: str
    churn_risk: bool = False
    champion_signal: bool = False


@dataclass
class TriageReport:
    """Weekly triage summary."""

    week_of: str
    total_issues: int
    issues: list[TriagedIssue] = field(default_factory=list)
    churn_risks: list[str] = field(default_factory=list)
    champions: list[str] = field(default_factory=list)
    sentiment_breakdown: dict[str, int] = field(default_factory=dict)
    category_breakdown: dict[str, int] = field(default_factory=dict)
    product_area_breakdown: dict[str, int] = field(default_factory=dict)


class Sage:
    """
    Community Manager agent for GitHub issue triage and community health.

    Capabilities:
    - Triage incoming GitHub issues by priority, category, and product area
    - Analyze author sentiment and flag churn risks
    - Identify community champions (frequent contributors, helpful commenters)
    - Generate suggested first responses for each issue
    - Produce weekly triage reports with actionable recommendations

    Tools:
    1. github_issues_fetch — Pull recent issues from devrel-ai-agents repo
    2. github_comments_fetch — Pull comments on specific issues
    3. github_user_history — Analyze a user's contribution history
    4. sentiment_analyzer — Classify text sentiment
    5. issue_categorizer — Map issues to product areas and types
    6. churn_detector — Flag users showing departure signals
    7. champion_identifier — Score users on community contribution
    8. response_generator — Draft empathetic, helpful first responses
    9. label_suggester — Suggest GitHub labels based on content
    10. duplicate_detector — Find similar existing issues
    11. priority_scorer — Score issue urgency based on impact/frequency
    12. escalation_router — Route critical issues to the right team
    13. weekly_report_compiler — Aggregate triage data into reports
    14. notification_dispatcher — Alert team members about urgent issues
    """

    SYSTEM_PROMPT = """You are Sage, a community manager for OpenClaw, an open-source
system of 10 specialized AI agents that replaces a full DevRel + Sales team for DevTools
companies. OpenClaw provides orchestration (Atlas), an agent SDK, MCP tools,
a knowledge base, scoring/eval, prompt optimization, onboarding/docs, and security —
built on Claude SDK + MCP.

Your mission is to make every developer who interacts with OpenClaw feel heard,
helped, and valued. You triage issues with empathy and precision.

Triage principles:
1. EMPATHY FIRST — Acknowledge the person's frustration before diving into technical details
2. FAST RESPONSE — First response within 4 hours for critical, 24 hours for all others
3. ACCURATE ROUTING — Tag the right product area and team so nothing falls through cracks
4. CHURN PREVENTION — Flag users who show signs of giving up (repeated issues, frustrated tone)
5. CHAMPION CULTIVATION — Recognize users who help others, submit quality bug reports, or contribute PRs

Sentiment signals:
- CHURNING: "I'm switching to...", "This is the Nth time...", "I give up"
- FRUSTRATED: Multiple exclamation marks, ALL CAPS, words like "broken", "terrible"
- NEUTRAL: Standard bug reports, feature requests
- POSITIVE: "Love OpenClaw", "Great work", "This is exactly what I needed"

Champion signals:
- Helps other users in issues/Discourse
- Submits well-structured bug reports with reproduction steps
- Opens PRs or suggests fixes
- Shares OpenClaw content on social media"""

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        github_tools: Optional["GitHubTools"] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.github_tools = github_tools

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a community management task.

        Fetches GitHub issues, analyzes them, and produces triage output
        with sentiment analysis and risk flags.
        """
        logger.info(f"Sage executing: {task[:80]}...")

        # Fetch issues from GitHub (graceful fallback if no tools)
        raw_issues: list[GitHubIssue] = []
        if self.github_tools:
            try:
                raw_issues = await self.github_tools.fetch_recent_issues(days=7)
                # Filter out PRs
                raw_issues = [i for i in raw_issues if not i.is_pull_request]
            except Exception as exc:
                logger.warning(f"GitHub fetch failed: {exc}")

        # Triage each issue
        triaged: list[TriagedIssue] = []
        for issue in raw_issues:
            triaged.append(
                await self.triage_issue(
                    issue_number=issue.number,
                    title=issue.title,
                    body=issue.body,
                    author=issue.author,
                    comments_count=getattr(issue, "comments_count", 0) or 0,
                    reactions_total=getattr(issue, "reactions_total", 0) or 0,
                )
            )

        # Build breakdowns
        sentiment_breakdown: dict[str, int] = {}
        category_breakdown: dict[str, int] = {}
        product_area_breakdown: dict[str, int] = {}
        churn_risks: list[str] = []

        for t in triaged:
            sentiment_breakdown[t.sentiment.value] = (
                sentiment_breakdown.get(t.sentiment.value, 0) + 1
            )
            category_breakdown[t.category] = category_breakdown.get(t.category, 0) + 1
            product_area_breakdown[t.product_area] = (
                product_area_breakdown.get(t.product_area, 0) + 1
            )
            if t.churn_risk:
                churn_risks.append(t.author)

        return {
            "agent": "sage",
            "task": task,
            "issues": [
                {
                    "number": t.issue_number,
                    "title": t.title,
                    "author": t.author,
                    "priority": t.priority.value,
                    "sentiment": t.sentiment.value,
                    "category": t.category,
                    "product_area": t.product_area,
                    "summary": t.summary,
                    "suggested_response": t.suggested_response,
                    "churn_risk": t.churn_risk,
                }
                for t in triaged
            ],
            "churn_risks": churn_risks,
            "champions": self._identify_champions(triaged),
            "sentiment_breakdown": sentiment_breakdown,
            "category_breakdown": category_breakdown,
            "product_area_breakdown": product_area_breakdown,
            "status": "triaged",
        }

    async def triage_issue(
        self,
        issue_number: int,
        title: str,
        body: str,
        author: str,
        comments_count: int = 0,
        reactions_total: int = 0,
    ) -> TriagedIssue:
        """Triage a single GitHub issue."""
        # Analyze sentiment
        sentiment = self._analyze_sentiment(body)

        # Detect churn risk
        churn_risk = sentiment == SentimentScore.CHURNING

        # Categorize
        category = self._categorize_issue(title, body)
        product_area = self._detect_product_area(title, body)
        priority = self._score_priority(title, body, sentiment)

        # Champion detection — high-engagement issues / PR-referencing bodies
        # are candidate champions for downstream identification.
        champion = self._detect_champion_signal(
            body=body,
            comments_count=comments_count,
            reactions_total=reactions_total,
        )

        return TriagedIssue(
            issue_number=issue_number,
            title=title,
            author=author,
            priority=priority,
            sentiment=sentiment,
            category=category,
            product_area=product_area,
            summary=f"[{priority.value.upper()}] {category} in {product_area}",
            suggested_response=self._draft_response(
                title, category, priority, sentiment, author,
            ),
            churn_risk=churn_risk,
            champion_signal=champion,
        )

    # Module-level threshold map — tuned high enough that random noise
    # doesn't trigger champion-flagging, low enough that genuine community
    # engagement is caught.
    CHAMPION_THRESHOLDS = {
        "comments_count": 3,
        "reactions_total": 5,
    }

    def _detect_champion_signal(
        self,
        body: str,
        comments_count: int = 0,
        reactions_total: int = 0,
    ) -> bool:
        """Return True if the issue shows champion-grade engagement.

        A "champion signal" means the user is going beyond just filing —
        attracting community discussion (comments), strong reactions, or
        referencing a PR they opened to fix the issue. Used by
        ``_identify_champions`` downstream.
        """
        if (comments_count or 0) >= self.CHAMPION_THRESHOLDS["comments_count"]:
            return True
        if (reactions_total or 0) >= self.CHAMPION_THRESHOLDS["reactions_total"]:
            return True
        body_lower = (body or "").lower()
        if "pr #" in body_lower or "#pull" in body_lower or "pull/" in body_lower:
            return True
        return False

    def _analyze_sentiment(self, text: str) -> SentimentScore:
        """Rule-based sentiment pre-filter before LLM analysis."""
        text_lower = text.lower()
        churn_signals = ["switching to", "give up", "moving away", "nth time"]
        frustration_signals = ["broken", "terrible", "worst", "!!!"]

        if any(signal in text_lower for signal in churn_signals):
            return SentimentScore.CHURNING
        if any(signal in text_lower for signal in frustration_signals):
            return SentimentScore.FRUSTRATED
        if any(word in text_lower for word in ["love", "great", "awesome", "thanks"]):
            return SentimentScore.POSITIVE
        return SentimentScore.NEUTRAL

    def _categorize_issue(self, title: str, body: str) -> str:
        """Categorize issue type based on content."""
        text = f"{title} {body}".lower()
        if any(w in text for w in ["bug", "error", "crash", "broken", "fix"]):
            return "bug"
        if any(w in text for w in ["feature", "request", "would be nice", "suggestion"]):
            return "feature_request"
        if any(w in text for w in ["how to", "question", "help", "?"]):
            return "question"
        if any(w in text for w in ["docs", "documentation", "readme", "typo"]):
            return "docs"
        if any(w in text for w in ["slow", "performance", "latency", "timeout"]):
            return "performance"
        return "bug"

    def _detect_product_area(self, title: str, body: str) -> str:
        """Map issue to OpenClaw product area."""
        text = f"{title} {body}".lower()
        areas = {
            "orchestration": ["orchestrat", "atlas", "pipeline", "weekly cycle", "delegation", "hub", "spoke"],
            "agent_sdk": ["agent sdk", "sdk", "claude sdk", "agent framework", "base agent", "execute"],
            "mcp_tools": ["mcp", "tool", "json-rpc", "stdio", "tool definition", "manifest"],
            "knowledge_base": ["knowledge base", "knowledge", "docs", "markdown", "rglob", "indexing"],
            "scoring_eval": ["score", "scoring", "eval", "evaluation", "metrics", "benchmark", "test"],
            "prompt_optimization": ["prompt", "template", "optimization", "tuning", "generation"],
            "onboarding_docs": ["onboarding", "documentation", "tutorial", "guide", "getting started", "setup"],
            "security": ["security", "auth", "permission", "token", "secret", "vulnerability", "access"],
        }
        for area, keywords in areas.items():
            if any(kw in text for kw in keywords):
                return area
        return "orchestration"  # default

    @staticmethod
    def _identify_champions(triaged: list[TriagedIssue]) -> list[str]:
        """Identify community champions from triaged issues.

        Champions are users with positive sentiment, helpful contributions,
        or multiple quality issue reports.
        """
        author_signals: dict[str, int] = {}
        for issue in triaged:
            author = issue.author
            score = 0
            if issue.sentiment == SentimentScore.POSITIVE:
                score += 2
            if issue.champion_signal:
                score += 3
            if issue.category in ("feature_request", "question"):
                score += 1  # Engaged users file features/questions
            if score > 0:
                author_signals[author] = author_signals.get(author, 0) + score

        # Champions = authors with score >= 3
        return [author for author, score in author_signals.items() if score >= 3]

    def _score_priority(self, title: str, body: str, sentiment: SentimentScore) -> IssuePriority:
        """Score issue priority based on content and sentiment."""
        text = f"{title} {body}".lower()
        if any(w in text for w in ["data loss", "security", "vulnerability", "crash"]):
            return IssuePriority.CRITICAL
        if sentiment == SentimentScore.CHURNING:
            return IssuePriority.HIGH
        if any(w in text for w in ["broken", "cannot", "unable", "blocking"]):
            return IssuePriority.HIGH
        if sentiment == SentimentScore.FRUSTRATED:
            return IssuePriority.MEDIUM
        return IssuePriority.LOW

    def _draft_response(
        self,
        title: str,
        category: str,
        priority: IssuePriority,
        sentiment: SentimentScore = SentimentScore.NEUTRAL,
        author: str = "",
    ) -> str:
        """Draft a suggested first response.

        Branch order matters: a CHURNING user with a CRITICAL bug should
        receive the empathetic response, not the templated critical one,
        so we check sentiment first.
        """
        # CHURNING comes BEFORE CRITICAL on purpose — frustrated users
        # need empathy first, not a templated triage notice.
        if sentiment == SentimentScore.CHURNING:
            handle = f"@{author} " if author else ""
            return (
                f"Hey {handle}— I hear you, and I'm sorry this has been frustrating. "
                f"This is on me to help you fix. Can you share: (1) what version "
                f"you're on, (2) the exact error or behavior you're seeing, and "
                f"(3) what you've already tried? I'll dig in personally."
            )
        if priority == IssuePriority.CRITICAL:
            return (
                "Thanks for reporting this — we're treating this as critical priority. "
                "Our team is investigating now. Could you share your OpenClaw version "
                "and any relevant error logs?"
            )
        if category == "question":
            return (
                "Great question! Let me point you to the relevant docs. "
                "If those don't fully answer it, let us know and we'll dig deeper."
            )
        return (
            "Thanks for raising this! We've added it to our triage queue. "
            "A team member will follow up shortly."
        )
