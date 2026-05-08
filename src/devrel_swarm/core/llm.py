"""Shared Anthropic LLM client wrapper for all agents."""

import asyncio
import json
import logging
from collections.abc import Awaitable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import AsyncAnthropic

from devrel_swarm.core.base import strip_markdown_fences

logger = logging.getLogger(__name__)

_current_agent_var: ContextVar[str] = ContextVar("devrel_swarm_current_agent", default="")

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_MAX_TOKENS = 4096

# Model catalog for multi-model routing
MODELS = {
    "opus": "claude-opus-4-0-20250514",
    "sonnet": DEFAULT_MODEL,
    "haiku": "claude-haiku-4-5-20251001",
}

# Cost per million tokens (USD) — used for budget tracking
MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-opus-4-0-20250514": {"input": 15.0, "output": 75.0},
    DEFAULT_MODEL: {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}

_CRITIQUE_CRITERIA: dict[str, str] = {
    "content": (
        "1. ACCURACY — Are claims grounded in the provided context? Any hallucinated facts?\n"
        "2. CLARITY — Is the writing clear, scannable, and free of jargon-for-jargon's-sake?\n"
        "3. ACTIONABILITY — Does the reader leave with something concrete to do?\n"
        "4. STRUCTURE — Logical flow, good heading hierarchy, appropriate length?\n"
        "5. VOICE — Developer-authentic, not marketing fluff or AI slop?\n"
        "6. CODE QUALITY — Are code examples complete, correct, and well-commented?"
    ),
    "sales": (
        "1. ACCURACY — Are claims grounded in product facts? No overpromising?\n"
        "2. CLARITY — Is the message scannable, short paragraphs, no filler?\n"
        "3. PERSUASIVENESS — Does it sell the next step, not the whole product?\n"
        "4. PERSONALIZATION — Does it reference the recipient's specific situation?\n"
        "5. VOICE — Developer-aware, not corporate marketing speak?\n"
        "6. CTA — One clear, low-friction call to action?"
    ),
    "marketing": (
        "1. ACCURACY — Are claims grounded in product knowledge base?\n"
        "2. CLARITY — Short paragraphs, clear hierarchy, mobile-readable?\n"
        "3. DIFFERENTIATION — Does it position against alternatives with evidence?\n"
        "4. STRUCTURE — Appropriate format for the content type (blog/landing/social)?\n"
        "5. VOICE — Developer-authentic, storytelling over selling?\n"
        "6. CTA — One clear next step per piece?"
    ),
}

CRITIQUE_PROMPT = """You are a senior content editor. Review the following draft and
provide a structured critique as JSON.

## Draft
{draft}

## Evaluation Criteria
{criteria}

Return ONLY a JSON object:
{{
  "overall_score": <1-10>,
  "issues": [
    {{"criterion": "...", "severity": "high|medium|low", "description": "...", "fix": "..."}}
  ],
  "strengths": ["..."]
}}"""

REVISE_PROMPT = """Revise the following draft by applying all editorial feedback.
For fixes that require additions (missing sections, examples), add them.
For fixes that require cuts (over-long paragraphs, buzzwords), remove them.
For fixes that require rewrites, rewrite only the affected section.
Do not change sections that have no associated issues.
Return ONLY the revised content, no preamble or commentary.

## Original Draft
{draft}

## Editorial Feedback
{critique}

## Revised Draft"""


@dataclass
class CritiqueResult:
    """Result of an editorial critique."""

    overall_score: int = 0
    issues: list[dict[str, str]] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)

    @property
    def revision_needed(self) -> bool:
        """Computed: revise if score < 7 or any high-severity issue exists."""
        if self.overall_score < 7:
            return True
        return any(i.get("severity") == "high" for i in self.issues)

    @classmethod
    def from_json(cls, raw: str) -> "CritiqueResult":
        cleaned = strip_markdown_fences(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return cls(overall_score=5)
        return cls(
            overall_score=data.get("overall_score", 5),
            issues=data.get("issues", []),
            strengths=data.get("strengths", []),
        )


@dataclass
class RevisionTrace:
    """Tracks the full revision history for a generation."""

    drafts: list[str] = field(default_factory=list)
    critiques: list[CritiqueResult] = field(default_factory=list)
    final_score: int = 0
    revision_rounds: int = 0


@dataclass
class TokenUsage:
    """Tracks cumulative token usage, cost, and per-agent breakdown."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    total_cost_usd: float = 0.0
    per_agent: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        agent: str = "",
        model: str = "",
    ) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1

        # Compute cost
        costs = MODEL_COSTS.get(model, MODEL_COSTS[DEFAULT_MODEL])
        call_cost = (
            input_tokens * costs["input"] / 1_000_000 + output_tokens * costs["output"] / 1_000_000
        )
        self.total_cost_usd += call_cost

        if agent:
            if agent not in self.per_agent:
                self.per_agent[agent] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                    "cost_usd": 0.0,
                }
            self.per_agent[agent]["input_tokens"] += input_tokens
            self.per_agent[agent]["output_tokens"] += output_tokens
            self.per_agent[agent]["calls"] += 1
            self.per_agent[agent]["cost_usd"] += call_cost

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_calls": self.total_calls,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "per_agent": {
                k: {**v, "cost_usd": round(v["cost_usd"], 4)} for k, v in self.per_agent.items()
            },
        }


class LLMClient:
    """Async Anthropic client with multi-model routing and budget enforcement.

    Supports per-call model overrides for cost optimization:
    - Use "haiku" for classification/extraction tasks
    - Use "opus" for high-quality content generation
    - Use default "sonnet" for everything else

    Budget enforcement: when total spend exceeds ``budget_limit_usd``,
    automatically downgrades to Haiku for remaining calls.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        budget_limit_usd: float = 0.0,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.budget_limit_usd = budget_limit_usd
        self.usage = TokenUsage()
        self._current_agent: str = ""
        self._budget_exhausted = False
        self._cost_sink: "Callable[[str, str, dict[str, Any]], Awaitable[None]] | None" = None
        self._client = AsyncAnthropic(api_key=api_key or "dummy")

    def _resolve_model(self, model_override: str | None) -> str:
        """Resolve the model to use for a call.

        Priority: budget downgrade > explicit override > default.
        """
        if self._budget_exhausted:
            return MODELS["haiku"]
        if model_override:
            return MODELS.get(model_override, model_override)
        return self.model

    def _check_budget(self) -> None:
        """Check if budget limit has been exceeded."""
        if (
            self.budget_limit_usd > 0
            and not self._budget_exhausted
            and self.usage.total_cost_usd >= self.budget_limit_usd * 0.95
        ):
            self._budget_exhausted = True
            logger.warning(
                "budget_limit_reached",
                extra={
                    "spent": round(self.usage.total_cost_usd, 4),
                    "limit": self.budget_limit_usd,
                    "action": "downgrading to haiku",
                },
            )

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """Send a prompt and return the response text.

        Args:
            model: Optional model override — "haiku", "sonnet", "opus",
                or a full model ID. If budget is exhausted, forced to haiku.
        """
        resolved_model = self._resolve_model(model)
        response = await self._client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text
        self.usage.record(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            agent=self._current_agent,
            model=resolved_model,
        )
        self._check_budget()
        await self._emit_cost(
            model=resolved_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0)
            or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        logger.info(
            "llm_call",
            extra={
                "agent": _current_agent_var.get() or self._current_agent or "unknown",
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": resolved_model,
                "cost_usd": round(self.usage.total_cost_usd, 4),
                "cumulative_calls": self.usage.total_calls,
            },
        )
        return text

    def set_agent(self, agent_name: str) -> None:
        """Set the current agent name for per-agent cost tracking."""
        self._current_agent = agent_name

    @contextmanager
    def agent_context(self, agent_name: str):
        """Set the cost-attribution agent for the duration of this context.

        Async-task-local via ContextVar — safe under asyncio.gather() unlike
        set_agent(), which mutates a shared instance attribute. Prefer this
        over set_agent() when running agents concurrently.
        """
        token = _current_agent_var.set(agent_name)
        try:
            yield
        finally:
            _current_agent_var.reset(token)

    def set_cost_sink(
        self,
        sink: "Callable[[str, str, dict[str, Any]], Awaitable[None]] | None",
    ) -> None:
        """Register async callback ``(agent, model, usage_dict) -> None``.

        Called once per successful Anthropic API response. ``None`` clears
        the sink. Sink exceptions are caught and logged at WARNING — they
        never break the LLM call (cost recording is best-effort).
        """
        self._cost_sink = sink

    async def _emit_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        if self._cost_sink is None:
            return
        # Shield the sink call so an outer cancellation (e.g. Atlas's per-agent
        # timeout firing between the API response and this write) doesn't drop
        # the cost row. The Anthropic call already returned and we've been billed;
        # the sink coroutine has no inner awaits, so once the event loop schedules
        # it, the SQLite commit completes synchronously even if the calling task
        # is being torn down. CancelledError is BaseException in 3.8+ so it
        # bypasses `except Exception`; re-raise it to preserve cancellation
        # semantics for the caller.
        coro = self._cost_sink(
            _current_agent_var.get() or self._current_agent or "unknown",
            model,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
            },
        )
        try:
            await asyncio.shield(coro)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("cost sink raised; ignoring: %s", e)

    async def critique(
        self,
        draft: str,
        content_type: str = "content",
    ) -> CritiqueResult:
        """Run editorial critique on a draft, return structured feedback.

        Args:
            draft: The content to critique.
            content_type: One of "content" (tutorials/blogs), "sales"
                (outreach/battle cards), "marketing" (landing pages/social).
                Selects appropriate evaluation criteria.
        """
        criteria = _CRITIQUE_CRITERIA.get(content_type, _CRITIQUE_CRITERIA["content"])
        raw = await self.generate(
            system_prompt="You are a senior content editor.",
            user_prompt=CRITIQUE_PROMPT.format(
                draft=draft[:12000],
                criteria=criteria,
            ),
            temperature=0.3,
            max_tokens=2048,
        )
        return CritiqueResult.from_json(raw)

    async def generate_with_revision(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_rounds: int = 2,
        min_score: int = 7,
        content_type: str = "content",
    ) -> tuple[str, RevisionTrace]:
        """Generate content with a critique-then-revise loop.

        Produces a draft, critiques it, and revises if the score is below
        *min_score* or any high-severity issue is flagged. Repeats up to
        *max_rounds* times.

        Args:
            content_type: Selects critique criteria — "content", "sales",
                or "marketing".

        Returns the final content and the full revision trace.
        """
        trace = RevisionTrace()

        # Initial generation
        draft = await self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        trace.drafts.append(draft)

        for _round in range(max_rounds):
            crit = await self.critique(draft, content_type=content_type)
            trace.critiques.append(crit)
            trace.final_score = crit.overall_score

            if not crit.revision_needed and crit.overall_score >= min_score:
                break

            # Revise
            critique_text = json.dumps(
                {"issues": crit.issues, "strengths": crit.strengths},
                indent=2,
            )
            draft = await self.generate(
                system_prompt=system_prompt,
                user_prompt=REVISE_PROMPT.format(
                    draft=draft[:12000],
                    critique=critique_text,
                ),
                temperature=max(temperature - 0.1, 0.2),
                max_tokens=max_tokens,
            )
            trace.drafts.append(draft)
            trace.revision_rounds += 1

        return draft, trace

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
