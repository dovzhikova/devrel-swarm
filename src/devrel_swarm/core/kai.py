"""
Kai — Content Creator Agent

Produces technical tutorials, blog posts, and changelog announcements
grounded in the product knowledge base.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.base import get_kb_search, load_agent_prompt
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.quality import generate_with_pipeline
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.code_validator import CodeValidator
from devrel_swarm.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


@dataclass
class ContentPiece:
    """A generated content artifact."""

    title: str
    content_type: str  # tutorial, blog_post, changelog, social
    body: str
    metadata: dict[str, Any]
    grounding_sources: list[str]  # knowledge base files referenced


class Kai:
    """
    Content Creator agent specializing in developer-facing technical content.

    Capabilities:
    - Technical tutorials with working code examples
    - Blog posts covering product updates and best practices
    - Changelog announcements for new features
    - Content grounded in the knowledge base (not hallucinated)

    Tools:
    1. knowledge_base_search — Retrieve relevant docs for content grounding
    2. code_validator — Verify code examples compile and run
    3. seo_analyzer — Check content for search optimization
    """

    _DEFAULT_SYSTEM_PROMPT = """You are Kai, a technical content creator for OpenClaw,
an open-source system of 10 specialized AI agents that replaces a full DevRel + Sales
team for DevTools companies. OpenClaw covers community management, social
listening, feedback synthesis, growth experimentation, content creation, video
production, documentation generation, competitive intelligence, sales enablement,
and campaign marketing — all orchestrated through a hub-and-spoke architecture with
cross-agent data flow. Your role is to write developer-facing content that is:

1. TECHNICALLY ACCURATE — Every code example must work. Every API reference must
   be current. Ground all claims in the knowledge base.
2. DEVELOPER-FIRST — Write for engineers who value precision over marketing fluff.
   Show, don't tell. Code > prose.
3. SEO-AWARE — Structure content with clear H2/H3 hierarchy, include relevant
   keywords naturally, and write compelling meta descriptions.
4. ACTIONABLE — Every piece should leave the reader with something they can
   implement immediately.

Content types you produce:
- Step-by-step tutorials (1500-2500 words, working code, clear prerequisites)
- Blog posts (800-1200 words, opinionated, data-backed)
- Changelog announcements (200-400 words, what changed, why it matters, how to use it)
- Social posts (< 280 chars, hook + value + CTA)

Always cite which knowledge base documents you referenced."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        return load_agent_prompt("kai", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT)

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        search_tools: Optional[SearchTools] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.search_tools = search_tools
        self.code_validator = CodeValidator()
        self._kb = get_kb_search(
            knowledge_base_path,
            extra_stop_words=frozenset({
                "write", "technical", "tutorial", "addressing",
                "developer", "pain", "point",
            }),
        )

    def search_knowledge_base(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search the knowledge base for relevant documents.

        Delegates to the shared KnowledgeBaseSearch. Kept as a public method
        for backward compatibility with tests and external callers.
        """
        return self._kb.search(query, limit=max_results)

    def _extract_upstream_context(self, context: dict[str, Any] | None) -> dict[str, Any]:
        """Extract structured upstream context from SharedContext for content grounding."""
        extracted: dict[str, Any] = {
            "pain_points": [],
            "real_issues": [],
            "architecture_doc": "",
            "dex_summary": "",
            "previous_content_titles": [],
            "recurring_themes": [],
        }
        if not context:
            return extracted

        # Cross-run memory → dedup and trend detection
        if "previous_weeks" in context:
            prev = context["previous_weeks"]
            if isinstance(prev, list):
                for week in prev:
                    if isinstance(week, dict):
                        extracted["previous_content_titles"].extend(
                            week.get("content_titles", [])
                        )
                        extracted["recurring_themes"].extend(
                            week.get("top_themes", [])
                        )

        # Iris themes → pain points
        if "iris_themes" in context:
            themes = context["iris_themes"]
            if isinstance(themes, dict):
                for t in themes.get("themes", []):
                    if isinstance(t, dict):
                        extracted["pain_points"].append({
                            "title": t.get("title", ""),
                            "description": t.get("description", ""),
                            "category": t.get("category", ""),
                            "severity": t.get("severity", 0),
                            "issues": t.get("representative_issues", []),
                        })

        # Sage triage → real GitHub issues for examples
        if "sage_triage" in context:
            sage = context["sage_triage"]
            if isinstance(sage, dict):
                for issue in sage.get("issues", [])[:10]:
                    if isinstance(issue, dict):
                        extracted["real_issues"].append({
                            "number": issue.get("number"),
                            "title": issue.get("title", ""),
                            "category": issue.get("category", ""),
                            "product_area": issue.get("product_area", ""),
                        })

        # Dex docs → architecture and API reference for accuracy
        if "dex_docs" in context:
            dex = context["dex_docs"]
            if isinstance(dex, dict):
                extracted["architecture_doc"] = dex.get("architecture_doc", "")[:4000]
                extracted["dex_summary"] = dex.get("llm_summary", "")[:2000]

        return extracted

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
        content_type: str = "tutorial",
    ) -> dict[str, Any]:
        """
        Execute a content creation task.

        Uses LLMClient to generate content grounded in the knowledge base,
        with cross-agent context informing topic selection and framing.

        Grounding sources (in priority order):
        1. Knowledge base files (curated product docs)
        2. Dex's architecture analysis (actual repo structure)
        3. Iris's pain points (what developers actually struggle with)
        4. Sage's real issues (concrete GitHub issue titles/numbers)
        5. Official docs via GitMCP (live documentation)
        """
        logger.info(f"Kai executing: {task[:80]}...")

        # 1. Search knowledge base — cap total context to ~12K chars
        grounding_docs = self.search_knowledge_base(task, max_results=5)
        grounding_context = "\n\n".join(
            f"[Source: {doc['source']}]\n{doc['content']}" for doc in grounding_docs
        )[:12000]

        # 2. Fetch official docs from GitMCP (capped)
        official_docs = ""
        if self.search_tools:
            try:
                raw_docs = await self.search_tools.fetch_official_docs(task)
                official_docs = (raw_docs or "")[:4000]
            except Exception as exc:
                logger.warning(f"Official docs fetch failed: {exc}")

        # 3. Extract structured upstream context
        upstream = self._extract_upstream_context(context)
        pain_points = upstream["pain_points"]
        real_issues = upstream["real_issues"]
        arch_doc = upstream["architecture_doc"]
        dex_summary = upstream["dex_summary"]

        # Build pain points section
        pain_section = ""
        if pain_points:
            pain_section = "Top developer pain points (from community feedback this week):\n"
            for pp in pain_points[:5]:
                pain_section += (
                    f"- **{pp['title']}** (severity: {pp['severity']}, "
                    f"category: {pp['category']}): {pp['description'][:200]}\n"
                )
                if pp['issues']:
                    pain_section += f"  Related GitHub issues: {', '.join(f'#{i}' for i in pp['issues'][:3])}\n"

        # Build real issues section
        issues_section = ""
        if real_issues:
            issues_section = "Real GitHub issues developers filed this week:\n"
            for issue in real_issues[:8]:
                issues_section += (
                    f"- #{issue['number']}: {issue['title']} "
                    f"[{issue['product_area']}]\n"
                )

        # Build dedup section from cross-run memory
        prev_titles = upstream["previous_content_titles"]
        recurring = upstream["recurring_themes"]
        dedup_section = ""
        if prev_titles:
            dedup_section += "Content already produced in recent weeks (DO NOT repeat):\n"
            for t in prev_titles[:10]:
                dedup_section += f"- {t}\n"
        if recurring:
            unique_recurring = list(dict.fromkeys(recurring))[:5]
            dedup_section += "\nRecurring themes across weeks (consider deeper coverage):\n"
            for t in unique_recurring:
                dedup_section += f"- {t}\n"

        prompt = f"""Task: {task}

## Knowledge Base (AUTHORITATIVE — use these as ground truth)
{grounding_context if grounding_context else "No specific docs found."}

## Repository Architecture (from source code analysis)
{arch_doc if arch_doc else "No architecture analysis available."}
{f"Summary: {dex_summary}" if dex_summary else ""}

## Official Documentation Reference
{official_docs if official_docs else "No official docs fetched."}

## Community Context
{pain_section if pain_section else "No pain point data from upstream agents."}

{issues_section if issues_section else ""}

## Content History
{dedup_section if dedup_section else "No previous content history available."}

## CRITICAL INSTRUCTIONS
1. Every fact, command, file path, API endpoint, and code example MUST come from
   the Knowledge Base or Repository Architecture sections above. If you cannot find
   it in the context provided, do NOT invent it.
2. Use REAL installation commands from the knowledge base (e.g., the actual install
   script URL, actual CLI commands, actual configuration keys).
3. Reference REAL file paths and directory structures from the architecture analysis.
4. When showing code examples, base them on actual patterns from the source code.
5. Address the #1 pain point from the community context — this is what developers
   actually struggle with right now.
6. Include a "Common Issues" section that references REAL GitHub issue titles/numbers.
7. Structure: Prerequisites → Step-by-step → Verification → Troubleshooting → Next Steps.
8. Cite which knowledge base documents you referenced at the end.
9. Do NOT hallucinate URLs, endpoints, or configuration options that aren't in the context.
10. Do NOT repeat topics from the Content History section — pick a fresh angle or go deeper.
11. If a theme keeps recurring across weeks, produce advanced/deep-dive content instead of intro-level.
"""

        base_result = {
            "agent": "kai",
            "task": task,
            "grounding_sources": [doc["source"] for doc in grounding_docs],
            "pain_points_addressed": [pp["title"] for pp in pain_points[:3]],
            "real_issues_referenced": [i["number"] for i in real_issues[:5]],
            "status": "generated",
        }

        if self.llm_client:
            try:
                content, strengths, issues = await generate_with_pipeline(
                    llm_client=self.llm_client,
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=prompt,
                    content_type=content_type,
                    logger=logger,
                )
                base_result["content"] = content
                if issues and isinstance(issues[0], dict):
                    remaining_issues = [
                        i for i in issues
                        if isinstance(i, dict) and i.get("severity") == "high"
                    ]
                else:
                    remaining_issues = [
                        i for i in issues if isinstance(i, str) and i.strip()
                    ]
                base_result["revision"] = {
                    "strengths": strengths,
                    "remaining_issues": remaining_issues,
                }

                # Validate code blocks in generated content
                report = self.code_validator.validate_content(content)
                base_result["code_validation"] = {
                    "total_blocks": report.total_blocks,
                    "validated": report.validated,
                    "passed": report.passed,
                    "failed": report.failed,
                    "skipped": report.skipped,
                    "all_passed": report.all_passed,
                    "errors": [
                        {
                            "language": e.block.language,
                            "line": e.block.line_number,
                            "error": e.error,
                            "code_snippet": e.block.code[:200],
                        }
                        for e in report.errors
                    ],
                }
                if not report.all_passed:
                    logger.warning(
                        f"Code validation: {report.failed}/{report.validated} "
                        f"blocks failed syntax checks"
                    )
            except Exception as exc:
                logger.warning(f"Content generation failed: {exc}")
                base_result["prompt_used"] = prompt[:500]
        else:
            base_result["prompt_used"] = prompt[:500]

        return base_result

    async def write_tutorial(
        self,
        topic: str,
        target_sdk: str = "javascript",
        context: Optional[dict[str, Any]] = None,
        content_type: str = "tutorial",
    ) -> ContentPiece:
        """Generate a step-by-step technical tutorial."""
        task = (
            f"Write a step-by-step tutorial on: {topic}. "
            f"Target SDK: {target_sdk}. "
            f"Include prerequisites, working code examples, and next steps."
        )
        result = await self.execute(task, context, content_type=content_type)
        return ContentPiece(
            title=topic,
            content_type="tutorial",
            body=result.get("content", ""),
            metadata={"sdk": target_sdk, "word_count_target": 2000},
            grounding_sources=result.get("grounding_sources", []),
        )

    async def write_changelog(
        self,
        feature_name: str,
        context: Optional[dict[str, Any]] = None,
        content_type: str = "landing_page",
    ) -> ContentPiece:
        """Generate a changelog announcement for a new feature."""
        task = (
            f"Write a changelog announcement for: {feature_name}. "
            f"Cover what changed, why it matters, and how to use it."
        )
        result = await self.execute(task, context, content_type=content_type)
        return ContentPiece(
            title=f"New: {feature_name}",
            content_type="changelog",
            body=result.get("content", ""),
            metadata={"word_count_target": 300},
            grounding_sources=result.get("grounding_sources", []),
        )
