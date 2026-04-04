"""Typed return values for agent execute() methods."""

from typing import NotRequired, TypedDict


class SageTriageResult(TypedDict):
    agent: str
    status: str
    issues: list[dict]
    total_analyzed: int
    prompt_used: NotRequired[str]


class EchoSocialResult(TypedDict):
    agent: str
    status: str
    brand: str
    top_mentions: list[dict]
    total_mentions: int
    platforms: dict
    sentiment_overall: dict
    engagement_opportunities: list[dict]
    reputation_risks: list[dict]
    prompt_used: NotRequired[str]


class IrisThemesResult(TypedDict):
    agent: str
    status: str
    themes: list[dict]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class NovaExperimentResult(TypedDict):
    agent: str
    status: str
    experiments: list[dict]
    prompt_used: NotRequired[str]


class KaiContentResult(TypedDict):
    agent: str
    status: str
    content_type: NotRequired[str]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class RexCompetitiveResult(TypedDict):
    agent: str
    status: str
    task: str
    competitors_discovered: list[str]
    kb_sources: list[str]
    web_intel_sources: dict[str, int]
    upstream_social_mentions: NotRequired[int]
    upstream_community_issues: NotRequired[int]
    prompt_used: NotRequired[str]
    content: NotRequired[dict]


class PaxSalesResult(TypedDict):
    agent: str
    status: str
    asset_type: str
    prompt_used: NotRequired[str]
    content: NotRequired[str]


class MoxCampaignResult(TypedDict):
    agent: str
    status: str
    content_type: str
    prompt_used: NotRequired[str]
    content: NotRequired[str]


class InstantlyAnalyticsResult(TypedDict):
    agent: str
    status: str
    total_campaigns: int
    total_sent: int
    total_opened: int
    total_replied: int
    total_bounced: int
    avg_open_rate: float
    avg_reply_rate: float
    avg_bounce_rate: float
    per_campaign: list[dict]


class InstantlyRepliesResult(TypedDict):
    agent: str
    status: str
    total_replies: int
    categories: dict
    drafts: list[dict]
