"""
Kai — Content Creator Agent

Produces technical tutorials, blog posts, and changelog announcements
grounded in the product knowledge base.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.base import get_kb_search, load_agent_prompt
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.quality import generate_with_pipeline
from devrel_swarm.quality.editorial import AbortLoud
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
        return self._system_prompt

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
        self._system_prompt = load_agent_prompt(
            "kai", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT
        )
        self._kb = get_kb_search(
            knowledge_base_path,
            extra_stop_words=frozenset(
                {
                    "write",
                    "technical",
                    "tutorial",
                    "addressing",
                    "developer",
                    "pain",
                    "point",
                }
            ),
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
            "source_files": [],
            "api_paths": [],
            "content_brief": {},
            "previous_content_titles": [],
            "recurring_themes": [],
        }
        if not context:
            return extracted

        def symbols_for(module: dict[str, Any], limit: int = 8) -> list[Any]:
            symbols = module.get("symbols", [])
            if isinstance(symbols, list):
                return symbols[:limit]
            if symbols:
                return [symbols]
            return []

        # Cross-run memory → dedup and trend detection
        if "previous_weeks" in context:
            prev = context["previous_weeks"]
            if isinstance(prev, list):
                for week in prev:
                    if isinstance(week, dict):
                        extracted["previous_content_titles"].extend(week.get("content_titles", []))
                        extracted["recurring_themes"].extend(week.get("top_themes", []))

        # Iris themes → pain points
        if "iris_themes" in context:
            themes = context["iris_themes"]
            if isinstance(themes, dict):
                for t in themes.get("themes", []):
                    if isinstance(t, dict):
                        extracted["pain_points"].append(
                            {
                                "title": t.get("title", ""),
                                "description": t.get("description", ""),
                                "category": t.get("category", ""),
                                "severity": t.get("severity", 0),
                                "issues": t.get("representative_issues", []),
                            }
                        )

        # Sage triage → real GitHub issues for examples
        if "sage_triage" in context:
            sage = context["sage_triage"]
            if isinstance(sage, dict):
                for issue in sage.get("issues", [])[:10]:
                    if isinstance(issue, dict):
                        extracted["real_issues"].append(
                            {
                                "number": issue.get("number"),
                                "title": issue.get("title", ""),
                                "category": issue.get("category", ""),
                                "product_area": issue.get("product_area", ""),
                            }
                        )

        # Dex docs → architecture and API reference for accuracy
        if "dex_docs" in context:
            dex = context["dex_docs"]
            if isinstance(dex, dict):
                extracted["architecture_doc"] = dex.get("architecture_doc", "")[:4000]
                extracted["dex_summary"] = dex.get("llm_summary", "")[:2000]
                modules = dex.get("modules", [])
                if isinstance(modules, list):
                    extracted["source_files"] = [
                        {
                            "path": m.get("path", ""),
                            "language": m.get("language", ""),
                            "symbols": symbols_for(m),
                            "docstring": (m.get("docstring") or "")[:240],
                        }
                        for m in modules[:30]
                        if isinstance(m, dict) and m.get("path")
                    ]
                api_reference = dex.get("api_reference", {})
                if isinstance(api_reference, dict):
                    extracted["api_paths"] = list(api_reference.keys())[:20]

        brief = context.get("content_brief")
        if isinstance(brief, dict):
            extracted["content_brief"] = brief

        return extracted

    def _evidence_gaps(
        self,
        task: str,
        *,
        grounding_docs: list[dict[str, Any]],
        official_docs: str,
        upstream: dict[str, Any],
    ) -> list[str]:
        """Return blocking gaps that would make generated content ungrounded."""
        brief = upstream.get("content_brief") or {}
        grounding_text = "\n".join(
            str(doc.get("content", "")) + "\n" + str(doc.get("source", ""))
            for doc in grounding_docs
        )
        has_repo_evidence = bool(
            upstream.get("architecture_doc")
            or upstream.get("dex_summary")
            or upstream.get("source_files")
            or brief.get("source_files")
        )
        has_file_path_evidence = bool(
            upstream.get("source_files")
            or brief.get("source_files")
            or self._contains_file_path(grounding_text)
        )
        has_product_evidence = bool(grounding_docs or official_docs.strip())
        gaps: list[str] = []
        if not has_product_evidence and not has_repo_evidence:
            gaps.append("no knowledge-base, official-docs, or repository evidence")

        task_lower = task.lower()
        if self._requires_evidence(task_lower, ("pain point", "developer pain")) and not (
            upstream.get("pain_points") or brief.get("pain_point")
        ):
            gaps.append("task requires a developer pain point, but none was provided")
        if self._requires_evidence(task_lower, ("github issue", "real issue")) and not (
            upstream.get("real_issues") or brief.get("github_issues")
        ):
            gaps.append("task requires real GitHub issues, but none were provided")
        if self._requires_evidence(task_lower, ("file path", "source code")) and not has_file_path_evidence:
            gaps.append("task requires repository file paths, but no source-file evidence was provided")
        return gaps

    @staticmethod
    def _requires_evidence(task_lower: str, phrases: tuple[str, ...]) -> bool:
        """Whether task wording positively requires a class of evidence.

        Negative or conditional wording such as "avoid GitHub issues unless
        available" should not force an evidence-gate failure. The generation
        prompt already tells Kai not to invent missing evidence.
        """
        negation_markers = (
            "avoid",
            "without",
            "do not",
            "don't",
            "unless",
            "only if",
            "if available",
            "when available",
            "if provided",
            "when provided",
        )
        requirement_markers = (
            "include",
            "cite",
            "reference",
            "use",
            "mention",
            "based on",
            "grounded in",
            "with",
            "from",
        )
        for phrase in phrases:
            start = task_lower.find(phrase)
            while start != -1:
                window_start = max(0, start - 48)
                window_end = min(len(task_lower), start + len(phrase) + 72)
                window = task_lower[window_start:window_end]
                if any(marker in window for marker in negation_markers):
                    start = task_lower.find(phrase, start + len(phrase))
                    continue
                if any(marker in window for marker in requirement_markers):
                    return True
                start = task_lower.find(phrase, start + len(phrase))
        return False

    @staticmethod
    def _contains_file_path(text: str) -> bool:
        """Detect source-file path evidence in KB snippets or source names."""
        if not text:
            return False
        return bool(
            re.search(
                r"(?:^|[`\s])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|md|sql|toml|yaml|yml|go|rs|java|rb|php|swift)",
                text,
                flags=re.MULTILINE,
            )
        )

    @classmethod
    def _search_query_from_task(cls, task: str) -> str:
        """Drop guardrail-only clauses before KB retrieval.

        Phrases like "avoid GitHub issue claims unless available" constrain the
        output, but they are not the topic. Keeping them in the search query can
        swamp product terms and retrieve issue-tracking docs for unrelated asks.
        """
        evidence_phrases = ("github issue", "real issue", "pain point")
        clauses = re.split(r"(?<=[.!?])\s+", task)
        kept: list[str] = []
        for clause in clauses:
            lower = clause.lower()
            if any(phrase in lower for phrase in evidence_phrases) and not cls._requires_evidence(
                lower, evidence_phrases
            ):
                continue
            kept.append(clause)
        return " ".join(kept).strip() or task

    @staticmethod
    def _normalize_claim(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def _is_evidenced(self, value: str, evidence_text: str) -> bool:
        if not value:
            return False
        normalized_value = self._normalize_claim(value)
        normalized_evidence = self._normalize_claim(evidence_text)
        return bool(normalized_value and normalized_value in normalized_evidence)

    def _grounded_output_issues(
        self, content: str, evidence_text: str
    ) -> list[dict[str, str]]:
        """Find high-risk execution claims that are unsupported by evidence.

        This is intentionally conservative and deterministic. It focuses on
        failure modes that make content non-executable: invented helper APIs,
        unverified MCP tool names, undocumented scripts/imports, ungrounded REST
        endpoints, and native ClickHouse system tables presented as normal HogQL.
        """
        issues: list[dict[str, str]] = []

        def add(severity: str, issue: str, fix: str) -> None:
            if not any(existing["issue"] == issue for existing in issues):
                issues.append({"severity": severity, "issue": issue, "fix": fix})

        if re.search(r"\b\w*mcp_call\s*\(", content) and not self._is_evidenced(
            "mcp_call", evidence_text
        ):
            add(
                "high",
                "content uses an unsupported MCP wrapper function",
                "Replace invented MCP helper calls with evidenced REST endpoints, HogQL examples, or prose.",
            )

        for tool_name in sorted(set(re.findall(r"['\"](posthog:[A-Za-z0-9_-]+)['\"]", content))):
            if not self._is_evidenced(tool_name, evidence_text):
                add(
                    "high",
                    f"content references unsupported MCP tool `{tool_name}`",
                    "Remove the tool call or replace it with an API/table that appears in evidence.",
                )

        for script_path in sorted(set(re.findall(r"\b(?:scripts|bin)/[A-Za-z0-9_./-]+\.py\b", content))):
            if not self._is_evidenced(script_path, evidence_text):
                add(
                    "medium",
                    f"content references unsupported script `{script_path}`",
                    "Remove the script reference or replace it with an evidenced file path.",
                )

        for endpoint in sorted(set(re.findall(r"(?<!:)`?(/api/[A-Za-z0-9_/@{}<>.-]+/?)[`'\",)]?", content))):
            if not self._is_evidenced(endpoint, evidence_text):
                add(
                    "high",
                    f"content references unsupported endpoint `{endpoint}`",
                    "Use only API paths present in the evidence or describe the request generically.",
                )

        direct_only_tables = (
            "system.replicas",
            "system.parts",
            "system.replication_queue",
            "system.part_log",
        )
        direct_access_terms = ("direct clickhouse", "clickhouse client", "clickhouse cli")
        content_lower = content.lower()
        for table in direct_only_tables:
            if table in content_lower and not any(term in content_lower for term in direct_access_terms):
                add(
                    "high",
                    f"native ClickHouse table `{table}` is not marked as direct ClickHouse access only",
                    "Either remove it or explicitly state it requires direct ClickHouse access outside PostHog HogQL.",
                )

        import_patterns = (
            r"^\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\s+",
            r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)",
        )
        for pattern in import_patterns:
            for module in sorted(set(re.findall(pattern, content, flags=re.MULTILINE))):
                if module.startswith(("posthog.", "products.")) and not self._is_evidenced(
                    f"from {module}", evidence_text
                ) and not self._is_evidenced(
                    f"import {module}", evidence_text
                ):
                    add(
                        "medium",
                        f"content imports unsupported internal module `{module}`",
                        "Use the module as a referenced file path, not as runnable guidance, unless the import is evidenced.",
                    )

        return issues

    async def _rewrite_ungrounded_content(
        self,
        *,
        content: str,
        issues: list[dict[str, str]],
        evidence_text: str,
        content_type: str,
    ) -> str:
        issue_lines = "\n".join(
            f"- {issue['severity']}: {issue['issue']} Fix: {issue['fix']}" for issue in issues
        )
        prompt = f"""Rewrite the draft to remove unsupported execution claims.

Hard requirements:
- Use only APIs, endpoints, tables, imports, scripts, and file paths present in the evidence.
- Delete invented MCP helpers and MCP tool names unless they appear in evidence.
- Prefer verified REST endpoints and HogQL tables over wrapper functions.
- Treat native ClickHouse system tables as direct ClickHouse access only, not normal PostHog HogQL.
- If an implementation detail is internal, describe it as a file to inspect rather than a public API to import.
- Return only the revised content.

Grounding issues to fix:
{issue_lines}

Evidence:
{evidence_text[:12000]}

Draft:
{content}
"""
        return await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.2,
            max_tokens=5000,
            model="sonnet" if content_type in {"tutorial", "blog_post"} else None,
        )

    @staticmethod
    def _code_validation_payload(report: Any) -> dict[str, Any]:
        return {
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
        search_query = self._search_query_from_task(task)
        raw_grounding_docs = self.search_knowledge_base(search_query, max_results=5)
        task_lower = task.lower()
        grounding_docs = [
            doc
            for doc in raw_grounding_docs
            if float(doc.get("relevance", 0) or 0) > 0
            or (
                self._requires_evidence(task_lower, ("file path", "source code"))
                and self._contains_file_path(
                    f"{doc.get('content', '')}\n{doc.get('source', '')}"
                )
            )
        ]
        grounding_context = "\n\n".join(
            f"[Source: {doc['source']}]\n{doc['content']}" for doc in grounding_docs
        )[:12000]

        # 2. Fetch official docs from GitMCP (capped)
        official_docs = ""
        if self.search_tools:
            try:
                raw_docs = await self.search_tools.fetch_official_docs(search_query)
                official_docs = (raw_docs or "")[:4000]
            except Exception as exc:
                logger.warning(f"Official docs fetch failed: {exc}")

        # 3. Extract structured upstream context
        upstream = self._extract_upstream_context(context)
        pain_points = upstream["pain_points"]
        real_issues = upstream["real_issues"]
        arch_doc = upstream["architecture_doc"]
        dex_summary = upstream["dex_summary"]
        source_files = upstream["source_files"]
        api_paths = upstream["api_paths"]
        content_brief = upstream["content_brief"]

        # Build pain points section
        pain_section = ""
        if pain_points:
            pain_section = "Top developer pain points (from community feedback this week):\n"
            for pp in pain_points[:5]:
                pain_section += (
                    f"- **{pp['title']}** (severity: {pp['severity']}, "
                    f"category: {pp['category']}): {pp['description'][:200]}\n"
                )
                if pp["issues"]:
                    pain_section += (
                        f"  Related GitHub issues: {', '.join(f'#{i}' for i in pp['issues'][:3])}\n"
                    )

        # Build real issues section
        issues_section = ""
        if real_issues:
            issues_section = "Real GitHub issues developers filed this week:\n"
            for issue in real_issues[:8]:
                issues_section += (
                    f"- #{issue['number']}: {issue['title']} [{issue['product_area']}]\n"
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

        source_section = ""
        if source_files:
            source_section = "Repository files Dex identified as usable evidence:\n"
            for item in source_files[:12]:
                symbols = ", ".join(str(s) for s in item.get("symbols", [])[:5])
                detail = f" — {symbols}" if symbols else ""
                source_section += f"- {item.get('path', '')}{detail}\n"
        if api_paths:
            source_section += "\nAPI/reference paths Dex identified:\n"
            for path in api_paths[:12]:
                source_section += f"- {path}\n"

        brief_section = ""
        if content_brief:
            brief_section = json.dumps(content_brief, indent=2, default=str)[:4000]

        prompt = f"""Task: {task}

## Knowledge Base (AUTHORITATIVE — use these as ground truth)
{grounding_context if grounding_context else "No specific docs found."}

## Repository Architecture (from source code analysis)
{arch_doc if arch_doc else "No architecture analysis available."}
{f"Summary: {dex_summary}" if dex_summary else ""}

## Source Evidence
{source_section if source_section else "No source file evidence available."}

## Official Documentation Reference
{official_docs if official_docs else "No official docs fetched."}

## Content Brief
{brief_section if brief_section else "No explicit content brief available."}

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
   Prefer documented REST endpoints, HogQL queries, and existing file paths over
   invented helper functions. Never invent MCP tool names or wrapper functions.
5. Address a community pain point only when one is present in the Community Context.
6. Reference GitHub issue titles/numbers only when real issues are present in the
   Community Context. If none are present, omit issue references entirely.
7. Structure: Prerequisites → Step-by-step → Verification → Troubleshooting → Next Steps.
8. Cite which knowledge base documents you referenced at the end.
9. Do NOT hallucinate URLs, endpoints, or configuration options that aren't in the context.
10. Do NOT repeat topics from the Content History section — pick a fresh angle or go deeper.
11. If a theme keeps recurring across weeks, produce advanced/deep-dive content instead of intro-level.
12. If you mention a native ClickHouse system table such as system.replicas,
    system.parts, system.replication_queue, or system.part_log, clearly mark it
    as direct ClickHouse access only, not a PostHog HogQL table.
"""

        base_result = {
            "agent": "kai",
            "task": task,
            "grounding_sources": [doc["source"] for doc in grounding_docs],
            "pain_points_addressed": [pp["title"] for pp in pain_points[:3]],
            "real_issues_referenced": [i["number"] for i in real_issues[:5]],
            "status": "generated",
        }

        evidence_gaps = self._evidence_gaps(
            task,
            grounding_docs=grounding_docs,
            official_docs=official_docs,
            upstream=upstream,
        )
        if evidence_gaps:
            base_result["status"] = "insufficient_evidence"
            base_result["evidence_gaps"] = evidence_gaps
            base_result["prompt_used"] = prompt[:500]
            return base_result

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
                        i for i in issues if isinstance(i, dict) and i.get("severity") == "high"
                    ]
                else:
                    remaining_issues = [i for i in issues if isinstance(i, str) and i.strip()]
                base_result["revision"] = {
                    "strengths": strengths,
                    "remaining_issues": remaining_issues,
                }

                # Validate code blocks in generated content
                report = self.code_validator.validate_content(content)
                base_result["code_validation"] = self._code_validation_payload(report)
                if not report.all_passed:
                    logger.warning(
                        f"Code validation: {report.failed}/{report.validated} "
                        f"blocks failed syntax checks"
                    )

                evidence_text = "\n\n".join(
                    [
                        grounding_context,
                        arch_doc,
                        dex_summary,
                        source_section,
                        official_docs,
                        brief_section,
                    ]
                )
                grounding_issues = self._grounded_output_issues(content, evidence_text)
                if grounding_issues:
                    rewritten = await self._rewrite_ungrounded_content(
                        content=content,
                        issues=grounding_issues,
                        evidence_text=evidence_text,
                        content_type=content_type,
                    )
                    rewritten_issues = self._grounded_output_issues(rewritten, evidence_text)
                    rewritten_report = self.code_validator.validate_content(rewritten)
                    base_result["content"] = rewritten
                    base_result["code_validation"] = self._code_validation_payload(rewritten_report)
                    base_result["grounding_validation"] = {
                        "rewritten": True,
                        "initial_issues": grounding_issues,
                        "remaining_issues": rewritten_issues,
                        "all_passed": not rewritten_issues,
                    }
                    if rewritten_issues:
                        base_result["status"] = "blocked_by_grounding_gate"
                        base_result["content"] = ""
                        logger.warning(
                            "Kai grounding gate blocked content after rewrite: %s",
                            rewritten_issues,
                        )
                else:
                    base_result["grounding_validation"] = {
                        "rewritten": False,
                        "initial_issues": [],
                        "remaining_issues": [],
                        "all_passed": True,
                    }
            except AbortLoud as exc:
                logger.warning("Content generation blocked by quality gate: %s", exc)
                base_result["status"] = "blocked_by_quality_gate"
                base_result["error"] = str(exc)
                base_result["content"] = ""
                base_result["prompt_used"] = prompt[:500]
            except Exception as exc:
                logger.exception(f"Content generation failed: {exc}")
                base_result["status"] = "error"
                base_result["error"] = str(exc)
                base_result.setdefault("content", "")
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
