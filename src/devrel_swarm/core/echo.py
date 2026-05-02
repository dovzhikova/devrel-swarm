"""
Echo — Social Media Listener Agent

Monitors Reddit, Hacker News, and Twitter/X for brand mentions,
sentiment trends, and community conversations. Surfaces opportunities
for engagement and flags reputation risks.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.base import strip_markdown_fences
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


@dataclass
class SocialMention:
    """A single brand mention on social media."""

    platform: str  # "reddit", "hackernews", "twitter"
    title: str
    url: str
    author: str
    content: str
    sentiment: str  # "positive", "neutral", "negative"
    engagement: int  # upvotes, likes, points
    posted_at: str
    subreddit: Optional[str] = None  # Reddit-specific
    is_question: bool = False
    requires_response: bool = False


@dataclass
class PlatformSummary:
    """Aggregated stats for a single platform."""

    platform: str
    total_mentions: int
    sentiment_breakdown: dict[str, int]
    top_posts: list[SocialMention]
    engagement_total: int
    trending_topics: list[str]


@dataclass
class SocialListeningReport:
    """Complete social listening report across all platforms."""

    period: str
    brand: str
    total_mentions: int
    platforms: list[PlatformSummary]
    sentiment_overall: dict[str, int]
    engagement_opportunities: list[dict[str, str]]
    reputation_risks: list[dict[str, str]]
    top_mentions: list[SocialMention]


# Keywords that signal engagement opportunities
ENGAGEMENT_SIGNALS = [
    "looking for", "recommend", "alternative to", "anyone using",
    "how to", "best tool", "which is better", "should I use",
    "migrating from", "vs", "comparison",
]

# Keywords that signal reputation risks
RISK_SIGNALS = [
    "switching away", "terrible experience", "avoid", "broken",
    "data loss", "security issue", "not working", "worst",
    "cancelled", "refund", "scam", "regret",
]

REDDIT_SUBREDDITS = [
    "devtools", "selfhosted", "SaaS", "opensource",
]

# Subset of ENGAGEMENT_SIGNALS that specifically indicates a question from a
# user. Maintained as its own constant so that reordering ENGAGEMENT_SIGNALS
# doesn't silently change is_question detection.
QUESTION_SIGNALS: tuple[str, ...] = (
    "?",
    "how do",
    "how to",
    "what is",
    "why does",
    "is there",
    "can someone",
    "anyone know",
)


def _parse_result_date(result: Any) -> Optional[datetime]:
    """Best-effort parse of a search result's publication date.

    Search backends use different field names — Firecrawl exposes
    ``published_date``, Brave reports ``age``, others use ``date`` or
    ``created_at``. Returns None if no parseable date is found; the
    caller falls back to ``datetime.now()`` so downstream code never
    sees None.
    """
    for fld in ("published_date", "posted_at", "date", "created_at"):
        val = getattr(result, fld, None)
        if not val:
            continue
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None

# Platform-specific search query templates
PLATFORM_QUERIES = {
    "reddit": "site:reddit.com {brand}",
    "hackernews": "site:news.ycombinator.com {brand}",
    "twitter": "site:twitter.com OR site:x.com {brand}",
}


class Echo:
    """
    Social Media Listener agent for brand monitoring across platforms.

    Capabilities:
    - Monitor Reddit, Hacker News, and Twitter/X for brand mentions
    - Classify sentiment of social media posts
    - Identify engagement opportunities (questions, comparisons, recommendations)
    - Flag reputation risks (negative sentiment, churn signals)
    - Track trending topics and conversations about the product
    - Produce weekly social listening reports

    Tools:
    1. reddit_scanner — Search Reddit for brand mentions across subreddits
    2. hackernews_scanner — Search HN for brand mentions and discussions
    3. twitter_scanner — Search Twitter/X for brand mentions
    4. sentiment_classifier — Classify post sentiment
    5. engagement_detector — Flag posts that warrant a response
    6. risk_detector — Flag posts indicating reputation risk
    7. topic_extractor — Extract trending topics from mentions
    8. report_compiler — Generate social listening report
    """

    BRAND_ALIASES: dict[str, list[str]] = {
        "openclaw": ["openclaw", "open-claw", "open claw"],
    }

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        search_tools: Optional[SearchTools] = None,
        llm_client: Optional[LLMClient] = None,
        search_limit: int = 20,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.search_tools = search_tools
        self.llm_client = llm_client
        self.search_limit = search_limit

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a social listening task.

        Scans social platforms for brand mentions, analyzes sentiment,
        and surfaces engagement opportunities and reputation risks.
        """
        logger.info(f"Echo executing: {task[:80]}...")

        brand = "OpenClaw"
        mentions = await self._scan_all_platforms(brand)

        # Reclassify sentiment with LLM if available (much more accurate)
        await self._batch_classify_sentiment(mentions)

        platform_summaries = self._build_platform_summaries(mentions)
        engagement_ops = self._find_engagement_opportunities(mentions)
        risks = self._flag_reputation_risks(mentions)
        overall_sentiment = self._aggregate_sentiment(mentions)

        return {
            "agent": "echo",
            "task": task,
            "brand": brand,
            "total_mentions": len(mentions),
            "platforms": {
                ps.platform: {
                    "total_mentions": ps.total_mentions,
                    "sentiment_breakdown": ps.sentiment_breakdown,
                    "engagement_total": ps.engagement_total,
                    "trending_topics": ps.trending_topics,
                    "top_posts": [
                        {
                            "title": m.title,
                            "url": m.url,
                            "sentiment": m.sentiment,
                            "engagement": m.engagement,
                        }
                        for m in ps.top_posts[:3]
                    ],
                }
                for ps in platform_summaries
            },
            "sentiment_overall": overall_sentiment,
            "engagement_opportunities": engagement_ops,
            "reputation_risks": risks,
            "top_mentions": [
                {
                    "platform": m.platform,
                    "title": m.title,
                    "url": m.url,
                    "author": m.author,
                    "sentiment": m.sentiment,
                    "engagement": m.engagement,
                }
                for m in sorted(mentions, key=lambda m: m.engagement, reverse=True)[:10]
            ],
            "status": "scanned",
        }

    async def scan_weekly(
        self,
        brand: str = "OpenClaw",
        aliases: Optional[list[str]] = None,
    ) -> SocialListeningReport:
        """Run a full weekly social listening scan."""
        mentions = await self._scan_all_platforms(brand, aliases)
        platform_summaries = self._build_platform_summaries(mentions)
        engagement_ops = self._find_engagement_opportunities(mentions)
        risks = self._flag_reputation_risks(mentions)
        overall_sentiment = self._aggregate_sentiment(mentions)

        top_mentions = sorted(mentions, key=lambda m: m.engagement, reverse=True)[:10]

        return SocialListeningReport(
            period="weekly",
            brand=brand,
            total_mentions=len(mentions),
            platforms=platform_summaries,
            sentiment_overall=overall_sentiment,
            engagement_opportunities=engagement_ops,
            reputation_risks=risks,
            top_mentions=top_mentions,
        )

    async def _scan_all_platforms(
        self,
        brand: str,
        aliases: Optional[list[str]] = None,
    ) -> list[SocialMention]:
        """Scan all social platforms for brand mentions."""
        all_mentions: list[SocialMention] = []

        if not self.search_tools:
            logger.warning("No search tools configured — social scanning unavailable")
            return all_mentions

        brand_lower = brand.lower()
        known_aliases = self.BRAND_ALIASES.get(brand_lower, [brand_lower])
        if aliases:
            known_aliases = list(set(known_aliases + [a.lower() for a in aliases]))

        for platform, query_template in PLATFORM_QUERIES.items():
            try:
                query = query_template.format(brand=brand)
                results = await self.search_tools.web_search(query, limit=self.search_limit)
                for result in results:
                    mention = self._parse_search_result(result, platform, known_aliases)
                    if mention:
                        all_mentions.append(mention)
            except Exception as exc:
                logger.warning(f"Failed to scan {platform}: {exc}")

        return all_mentions

    def _parse_search_result(
        self,
        result: Any,
        platform: str,
        aliases: list[str],
    ) -> Optional[SocialMention]:
        """Parse a search result into a SocialMention if it mentions the brand."""
        text = f"{result.title} {result.snippet}".lower()

        # Check if brand is actually mentioned
        if not any(alias in text for alias in aliases):
            return None

        sentiment = self._classify_sentiment(text)
        is_question = any(signal in text for signal in QUESTION_SIGNALS)
        requires_response = is_question or sentiment == "negative"

        # Extract subreddit from URL if Reddit
        subreddit = None
        if platform == "reddit" and "/r/" in result.url:
            parts = result.url.split("/r/")
            if len(parts) > 1:
                subreddit = parts[1].split("/")[0]

        # Use the actual publication date from the result when available;
        # otherwise fall back to "now" (preserves prior behavior). Trend
        # detection is broken if every mention is timestamped with the
        # scrape time.
        parsed_date = _parse_result_date(result)
        posted_at = (parsed_date or datetime.now()).strftime("%Y-%m-%d")

        return SocialMention(
            platform=platform,
            title=result.title,
            url=result.url,
            author="",  # Not available from search results
            content=result.snippet,
            sentiment=sentiment,
            engagement=0,  # Not available from search results
            posted_at=posted_at,
            subreddit=subreddit,
            is_question=is_question,
            requires_response=requires_response,
        )

    def _classify_sentiment_rule_based(self, text: str) -> str:
        """Rule-based sentiment classification fallback."""
        text_lower = text.lower()

        negative_signals = [
            "terrible", "worst", "broken", "hate", "awful",
            "switching away", "avoid", "not working", "regret",
            "disappointed", "frustrated", "useless", "buggy",
        ]
        positive_signals = [
            "love", "great", "awesome", "amazing", "best",
            "recommend", "fantastic", "excellent", "solid",
            "impressed", "perfect", "incredible", "game changer",
        ]

        neg_count = sum(1 for s in negative_signals if s in text_lower)
        pos_count = sum(1 for s in positive_signals if s in text_lower)

        if neg_count > pos_count:
            return "negative"
        if pos_count > neg_count:
            return "positive"
        return "neutral"

    def _classify_sentiment(self, text: str) -> str:
        """Synchronous sentiment for single mentions (rule-based fallback)."""
        return self._classify_sentiment_rule_based(text)

    _SENTIMENT_BATCH_SIZE = 40

    async def _batch_classify_sentiment(
        self, mentions: list["SocialMention"],
    ) -> None:
        """Reclassify sentiment for all mentions using LLM in batched calls.

        Processes mentions in chunks of _SENTIMENT_BATCH_SIZE. Mutates
        mention.sentiment in place. Falls back to keeping the rule-based
        classification if an LLM call fails for a chunk.
        """
        if not self.llm_client or not mentions:
            return

        total_classified = 0
        for chunk_start in range(0, len(mentions), self._SENTIMENT_BATCH_SIZE):
            chunk = mentions[chunk_start:chunk_start + self._SENTIMENT_BATCH_SIZE]
            classified = await self._classify_sentiment_chunk(chunk)
            total_classified += classified

        if total_classified:
            logger.info(f"LLM sentiment classified {total_classified}/{len(mentions)} mentions")

    async def _classify_sentiment_chunk(
        self, chunk: list["SocialMention"],
    ) -> int:
        """Classify sentiment for a single chunk. Returns count of classified."""
        items = []
        for i, m in enumerate(chunk):
            items.append(f"{i}. [{m.platform}] {m.title[:100]} — {m.content[:150]}")
        items_text = "\n".join(items)

        prompt = f"""Classify the sentiment of each social media mention below.
Return a JSON array where each element is an object with "index" (int) and
"sentiment" (one of "positive", "neutral", "negative").

Mentions:
{items_text}

Consider nuance:
- Sarcasm: "love how broken this is" = negative
- Mixed sentiment: lean toward the dominant signal
- Developer frustration: "why doesn't it support X?" = neutral-negative
- Feature requests framed as complaints = neutral
- Content too short to determine = neutral

Return ONLY the JSON array, no commentary."""

        try:
            raw = await self.llm_client.generate(
                system_prompt=(
                    "You are a sentiment analyst specializing in developer community "
                    "discourse across Reddit, Hacker News, and Twitter/X."
                ),
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=2048,
                model="haiku",
            )
            cleaned = strip_markdown_fences(raw)
            results = json.loads(cleaned)
            classified = 0
            for item in results:
                idx = item.get("index", -1)
                sent = item.get("sentiment", "")
                if 0 <= idx < len(chunk) and sent in ("positive", "neutral", "negative"):
                    chunk[idx].sentiment = sent
                    classified += 1
            return classified
        except Exception as exc:
            logger.warning(f"LLM sentiment chunk failed, keeping rule-based: {exc}")
            return 0

    def _build_platform_summaries(
        self, mentions: list[SocialMention],
    ) -> list[PlatformSummary]:
        """Build per-platform summaries from all mentions."""
        by_platform: dict[str, list[SocialMention]] = {}
        for m in mentions:
            by_platform.setdefault(m.platform, []).append(m)

        summaries = []
        for platform, platform_mentions in by_platform.items():
            sentiment_breakdown: dict[str, int] = {}
            for m in platform_mentions:
                sentiment_breakdown[m.sentiment] = (
                    sentiment_breakdown.get(m.sentiment, 0) + 1
                )

            engagement_total = sum(m.engagement for m in platform_mentions)
            top_posts = sorted(
                platform_mentions, key=lambda m: m.engagement, reverse=True
            )[:5]

            # Extract topics from titles
            topics = self._extract_topics(platform_mentions)

            summaries.append(
                PlatformSummary(
                    platform=platform,
                    total_mentions=len(platform_mentions),
                    sentiment_breakdown=sentiment_breakdown,
                    top_posts=top_posts,
                    engagement_total=engagement_total,
                    trending_topics=topics,
                )
            )

        return summaries

    def _find_engagement_opportunities(
        self, mentions: list[SocialMention],
    ) -> list[dict[str, str]]:
        """Find posts that represent engagement opportunities."""
        opportunities = []
        for m in mentions:
            text = f"{m.title} {m.content}".lower()
            matched_signals = [s for s in ENGAGEMENT_SIGNALS if s in text]
            if matched_signals:
                opportunities.append({
                    "platform": m.platform,
                    "title": m.title,
                    "url": m.url,
                    "reason": f"Matches signals: {', '.join(matched_signals[:3])}",
                    "suggested_action": self._suggest_engagement_action(matched_signals),
                })
        return opportunities[:10]

    def _flag_reputation_risks(
        self, mentions: list[SocialMention],
    ) -> list[dict[str, str]]:
        """Flag posts that indicate reputation risks."""
        risks = []
        for m in mentions:
            text = f"{m.title} {m.content}".lower()
            matched_risks = [s for s in RISK_SIGNALS if s in text]
            if matched_risks:
                risks.append({
                    "platform": m.platform,
                    "title": m.title,
                    "url": m.url,
                    "severity": "high" if len(matched_risks) >= 2 else "medium",
                    "signals": matched_risks,
                })
        return risks[:10]

    def _aggregate_sentiment(
        self, mentions: list[SocialMention],
    ) -> dict[str, int]:
        """Aggregate sentiment across all mentions."""
        breakdown: dict[str, int] = {"positive": 0, "neutral": 0, "negative": 0}
        for m in mentions:
            breakdown[m.sentiment] = breakdown.get(m.sentiment, 0) + 1
        return breakdown

    def _extract_topics(
        self, mentions: list[SocialMention],
    ) -> list[str]:
        """Extract trending topics from mention titles."""
        topic_keywords = [
            "devrel", "developer relations", "developer advocacy", "community",
            "open source", "self-hosted", "ai agents", "multi-agent",
            "devtools", "developer experience", "sdk", "api",
            "orbit", "common room", "devrev", "chatwoot",
            "integration", "automation", "performance", "pricing",
        ]

        topic_counts: dict[str, int] = {}
        for m in mentions:
            text = f"{m.title} {m.content}".lower()
            for topic in topic_keywords:
                if topic in text:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1

        sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
        return [topic for topic, _ in sorted_topics[:5]]

    @staticmethod
    def _suggest_engagement_action(signals: list[str]) -> str:
        """Suggest what kind of engagement to do based on signals."""
        if any(s in signals for s in ["looking for", "recommend", "best tool"]):
            return "Share how OpenClaw addresses their need with a helpful, non-salesy comment"
        if any(s in signals for s in ["alternative to", "vs", "comparison"]):
            return "Provide an honest comparison highlighting OpenClaw's strengths vs Orbit, Common Room, DevRev, or Chatwoot"
        if any(s in signals for s in ["how to", "anyone using"]):
            return "Share relevant documentation or tutorial link"
        if any(s in signals for s in ["migrating from", "should I use"]):
            return "Offer migration guidance and link to getting-started docs"
        return "Engage with helpful, relevant information"
