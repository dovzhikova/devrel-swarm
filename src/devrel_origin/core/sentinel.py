"""
Sentinel — Brand Consistency Auditor Agent

Audits all agent outputs for brand voice consistency, messaging alignment,
ICP accuracy, and content quality. Produces a scored audit report with
specific remediation recommendations.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from devrel_origin.core.base import load_agent_prompt, strip_markdown_fences
from devrel_origin.core.llm import LLMClient
from devrel_origin.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)


# Per-agent map of content fields to scan. Each agent stores its prose
# under a different key (Mox under "blog_post", Pax under "body", etc.),
# so Sentinel must check each agent's actual primary field rather than
# assuming a universal "content" key. Order within the list = priority;
# the first non-empty value wins.
_AGENT_CONTENT_FIELDS: dict[str, list[str]] = {
    "kai_content": ["content", "body"],
    "mox_campaigns": [
        "blog_post",
        "landing_page",
        "social_batch",
        "campaign_brief",
        "content",
    ],
    "pax_sales": ["body", "battle_card", "sequence", "content"],
    "rex_competitive": ["analysis", "summary", "content"],
    "dex_docs": ["architecture", "api_reference", "content"],
    "iris_themes": ["recommendations", "content"],
    "vox_video": ["script", "content"],
    "sage_triage": ["content"],
    "echo_social": ["content"],
}


def _safe_json_loads(raw: str) -> dict[str, Any]:
    """Parse JSON even when an LLM wraps it in prose or markdown fences."""
    cleaned = strip_markdown_fences(raw or "").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}


@dataclass
class AuditItem:
    """Audit result for a single content piece."""

    agent: str
    content_type: str
    score: int  # 1-10
    passed: bool
    issues: list[dict[str, str]] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)


@dataclass
class BrandAuditReport:
    """Complete brand consistency audit."""

    overall_score: int  # 1-100
    items: list[AuditItem]
    voice_consistency: int  # 1-10
    icp_alignment: int  # 1-10
    messaging_coherence: int  # 1-10
    cross_piece_issues: list[str]
    recommendations: list[str]


class Sentinel:
    """
    Brand Consistency Auditor agent.

    Capabilities:
    - Audit generated content for brand voice adherence
    - Check ICP alignment across all outputs
    - Verify messaging consistency between agents
    - Score content quality on multiple dimensions
    - Produce remediation recommendations
    """

    _DEFAULT_SYSTEM_PROMPT = """You are Sentinel, a brand consistency auditor. \
You review all content produced by the agent system and flag deviations from \
brand standards.

Audit dimensions:
1. VOICE — Developer-authentic, not corporate marketing. No buzzwords, no fluff.
2. ICP ALIGNMENT — Content targets the right audience (DevTools founders, \
engineering leaders, developer advocates).
3. MESSAGING COHERENCE — All pieces tell a consistent story. No contradictions \
between what Kai's tutorial says and Mox's landing page claims.
4. TECHNICAL ACCURACY — Claims are grounded, code examples work, APIs exist.
5. CTA CONSISTENCY — Each piece has one clear CTA appropriate to its funnel stage.
6. FORMATTING — Short paragraphs, clear hierarchy, scannable structure.

Scoring:
- 9-10: Exceptional, publish immediately
- 7-8: Good, minor polish needed
- 5-6: Acceptable with edits
- 3-4: Significant issues, needs rewrite
- 1-2: Off-brand, reject

Be strict. Generic AI slop scores 3-4 regardless of technical accuracy."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        return load_agent_prompt(
            "sentinel",
            "system_prompt.txt",
            self._DEFAULT_SYSTEM_PROMPT,
        )

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
        """Run brand consistency audit on all generated content."""
        logger.info(f"Sentinel executing: {task[:80]}...")

        # Collect all content pieces from context
        pieces = self._collect_content(context)
        if not pieces:
            return {
                "agent": "sentinel",
                "task": task,
                "status": "no_content",
                "overall_score": 0,
                "message": "No content found to audit",
            }

        # Run LLM audit if available
        if self.llm_client:
            return await self._llm_audit(task, pieces)

        # Fallback: basic structural checks
        return self._structural_audit(task, pieces)

    def _collect_content(
        self,
        context: Any,
    ) -> list[dict[str, str]]:
        """Extract all content pieces from SharedContext for auditing.

        Walks ``_AGENT_CONTENT_FIELDS`` and picks the first non-empty
        candidate field per agent. Each agent has a different primary
        field (Mox stores under ``blog_post``, Pax under ``body``, etc.),
        so a universal "content" key would silently audit only Kai.
        """
        pieces: list[dict[str, str]] = []
        if not context:
            return pieces

        ctx_dict = context.to_dict() if hasattr(context, "to_dict") else dict(context)

        for context_key, candidate_fields in _AGENT_CONTENT_FIELDS.items():
            agent_data = ctx_dict.get(context_key, {})
            if not isinstance(agent_data, dict):
                continue
            for fld in candidate_fields:
                value = agent_data.get(fld)
                if isinstance(value, str) and value.strip():
                    pieces.append(
                        {
                            "agent": context_key,
                            "content_type": fld,
                            "content": value[:5000],
                        }
                    )
                    break  # one piece per agent
                if isinstance(value, list) and value:
                    joined = "\n\n".join(str(v) for v in value[:3])[:5000]
                    if joined.strip():
                        pieces.append(
                            {
                                "agent": context_key,
                                "content_type": fld,
                                "content": joined,
                            }
                        )
                        break

        return pieces

    async def _llm_audit(
        self,
        task: str,
        pieces: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Run comprehensive LLM-powered brand audit."""
        pieces_text = ""
        for p in pieces:
            pieces_text += (
                f"\n\n--- [{p['agent'].upper()} — {p['content_type']}] ---\n{p['content']}\n"
            )

        prompt = f"""Audit all content pieces below for brand consistency.

{pieces_text}

For each piece, evaluate:
1. Voice score (1-10): developer-authentic vs marketing fluff
2. ICP alignment (1-10): targets right audience
3. Technical accuracy (1-10): claims grounded, code correct
4. CTA clarity (1-10): one clear next step
5. Formatting (1-10): scannable, short paragraphs

Also evaluate cross-piece consistency:
- Do pieces contradict each other?
- Is the messaging aligned across all agents?
- Are the same features described the same way?

Return JSON:
{{
  "overall_score": <1-100>,
  "voice_consistency": <1-10>,
  "icp_alignment": <1-10>,
  "messaging_coherence": <1-10>,
  "items": [
    {{
      "agent": "...",
      "content_type": "...",
      "score": <1-10>,
      "passed": true/false,
      "issues": [{{"dimension": "...", "severity": "high|medium|low", "detail": "..."}}],
      "strengths": ["..."]
    }}
  ],
  "cross_piece_issues": ["..."],
  "recommendations": ["..."]
}}"""

        raw = ""
        try:
            raw = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.2,
                max_tokens=4096,
            )
            audit = _safe_json_loads(raw)
            return {
                "agent": "sentinel",
                "task": task,
                "status": "audited",
                **audit,
            }
        except json.JSONDecodeError as exc:
            logger.warning(
                "Sentinel LLM audit returned non-JSON response; marking audit_failed. "
                "error=%s raw_head=%r",
                exc,
                (raw or "")[:200],
            )
            logger.debug("Full raw response: %s", raw)
            return {
                "agent": "sentinel",
                "task": task,
                "status": "audit_failed",
                "overall_score": 0,
                "items": [],
                "cross_piece_issues": [],
                "recommendations": ["Retry Sentinel audit; model response was not valid JSON."],
                "error": str(exc),
            }
        except Exception as exc:
            logger.warning(
                "Sentinel LLM audit API error; falling back to structural: %s",
                exc,
            )
            return self._structural_audit(task, pieces)

    def _structural_audit(
        self,
        task: str,
        pieces: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Fallback: basic structural quality checks without LLM."""
        items = []
        total_score = 0

        for p in pieces:
            content = p["content"]
            issues = []
            score = 7  # Start at passing

            # Check paragraph length
            paragraphs = content.split("\n\n")
            long_paras = [pp for pp in paragraphs if len(pp.split()) > 100]
            if long_paras:
                issues.append(
                    {
                        "dimension": "formatting",
                        "severity": "medium",
                        "detail": f"{len(long_paras)} paragraphs exceed 100 words",
                    }
                )
                score -= 1

            # Check for heading structure
            if "## " not in content and "# " not in content:
                issues.append(
                    {
                        "dimension": "formatting",
                        "severity": "medium",
                        "detail": "No heading hierarchy found",
                    }
                )
                score -= 1

            # Check for buzzwords
            buzzwords = [
                "revolutionary",
                "game-changing",
                "cutting-edge",
                "best-in-class",
                "world-class",
                "synergy",
                "leverage",
                "disrupt",
                "paradigm",
            ]
            found_buzzwords = [b for b in buzzwords if b in content.lower()]
            if found_buzzwords:
                issues.append(
                    {
                        "dimension": "voice",
                        "severity": "high",
                        "detail": f"Marketing buzzwords found: {', '.join(found_buzzwords)}",
                    }
                )
                score -= 2

            items.append(
                {
                    "agent": p["agent"],
                    "content_type": p["content_type"],
                    "score": max(1, score),
                    "passed": score >= 6,
                    "issues": issues,
                }
            )
            total_score += max(1, score)

        # Map item average from 1-7 scale onto 10-100 scale linearly so the
        # structural fallback produces scores comparable to the LLM 1-100 path:
        #   item_avg = 1 → 10, item_avg = 4 → 55, item_avg = 7 → 100
        average_item = total_score / max(len(items), 1)
        overall = int(round(((average_item - 1) / 6) * 90 + 10))
        overall = max(0, min(100, overall))
        return {
            "agent": "sentinel",
            "task": task,
            "status": "audited_structural",
            "overall_score": overall,
            "items": items,
            "cross_piece_issues": [],
            "recommendations": [],
        }
