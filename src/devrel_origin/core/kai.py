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

from devrel_origin.core.base import get_kb_search, load_agent_prompt
from devrel_origin.core.llm import LLMClient
from devrel_origin.quality import generate_with_pipeline
from devrel_origin.quality.editorial import AbortLoud
from devrel_origin.tools.api_client import PostHogClient
from devrel_origin.tools.code_validator import CodeValidator
from devrel_origin.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)

_FILE_PATH_RE = re.compile(
    r"(?:^|[`\s(\[])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\."
    r"(?:py|ts|tsx|js|jsx|md|sql|toml|yaml|yml|xml|json|txt|log|ambr|go|rs|java|rb|php|swift))",
    re.IGNORECASE | re.MULTILINE,
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<!\w)(/(?:var|etc|opt|usr|tmp|home|srv|app|data)/[A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)
_CONFIG_NAME_RE = re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b")
_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO|DESCRIBE|DESC|TABLE)\s+`?"
    r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?Sources?(?:\*\*)?\s*:\s*(.+)$")
_SOURCE_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+sources?\b", re.IGNORECASE)
_INTERNAL_MARKER_RE = re.compile(
    r"\s*\((?:evidence|context)\s+truncated[^)]*\)|\b(?:evidence|context)\s+truncated\b[:;,.]?",
    re.IGNORECASE,
)
_UNSUPPORTED_PLACEHOLDER_RE = re.compile(r"\bYOUR(?:_[A-Z0-9]+)+\b")
_SQL_BLOCK_RE = re.compile(r"```sql\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```(\w*)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_CHECK_SECTION_RE = re.compile(
    r"(?ms)^###\s+Check\s+\d+:\s*(.*?)\n(.*?)(?=^###\s+Check\s+\d+:|^##\s+|\Z)"
)
_SQL_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_INTERNAL_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:from\s+((?:posthog|products)\.[A-Za-z0-9_.]+)\s+import|import\s+((?:posthog|products)\.[A-Za-z0-9_.]+))"
)
_SQL_IDENTIFIER_SKIP = {
    "and",
    "as",
    "by",
    "case",
    "desc",
    "distinct",
    "from",
    "group",
    "having",
    "in",
    "interval",
    "join",
    "limit",
    "not",
    "null",
    "on",
    "or",
    "order",
    "select",
    "table",
    "then",
    "where",
    "with",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "now",
    "clusterallreplicas",
    "siphash64",
}


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

    def search_knowledge_base(
        self,
        query: str,
        max_results: int = 5,
        content_truncate: int = 3000,
    ) -> list[dict[str, str]]:
        """Search the knowledge base for relevant documents.

        Delegates to the shared KnowledgeBaseSearch. Kept as a public method
        for backward compatibility with tests and external callers.
        """
        return self._kb.search(query, limit=max_results, content_truncate=content_truncate)

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
        if (
            self._requires_evidence(task_lower, ("file path", "source code"))
            and not has_file_path_evidence
        ):
            gaps.append(
                "task requires repository file paths, but no source-file evidence was provided"
            )
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
        return bool(_FILE_PATH_RE.search(text) or _ABSOLUTE_PATH_RE.search(text))

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

    @staticmethod
    def _extract_file_paths(text: str) -> list[str]:
        paths = [match.group(1).strip("`'\".,);]") for match in _FILE_PATH_RE.finditer(text)]
        paths.extend(
            match.group(1).strip("`'\".,);]") for match in _ABSOLUTE_PATH_RE.finditer(text)
        )
        return paths

    @staticmethod
    def _sanitize_internal_markers(text: str) -> str:
        """Remove generation-context leakage such as '(evidence truncated)'."""
        cleaned = _INTERNAL_MARKER_RE.sub("", text)
        cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _normalize_unsupported_placeholders(text: str) -> str:
        """Turn config-looking placeholder constants into reader placeholders."""

        def replace(match: re.Match[str]) -> str:
            value = match.group(0)
            if "API_KEY" in value:
                return "<your-api-key>"
            if "TOKEN" in value:
                return "<your-token>"
            if "PROJECT" in value:
                return "<project-id>"
            return "<value>"

        return _UNSUPPORTED_PLACEHOLDER_RE.sub(replace, text)

    @staticmethod
    def _source_citation_lines(content: str) -> list[str]:
        lines = content.splitlines()
        citation_lines = [match.group(1) for match in _SOURCE_LABEL_RE.finditer(content)]
        in_sources = False
        for line in lines:
            if _SOURCE_HEADING_RE.match(line):
                in_sources = True
                continue
            if in_sources and line.startswith("#"):
                in_sources = False
            if in_sources and line.strip():
                citation_lines.append(line)
        return citation_lines

    def _grounded_output_issues(
        self,
        content: str,
        evidence_text: str,
        *,
        allowed_source_ids: list[str] | None = None,
        task: str = "",
    ) -> list[dict[str, str]]:
        """Find high-risk execution claims that are unsupported by evidence.

        This is intentionally conservative and deterministic. It focuses on
        failure modes that make content non-executable: invented helper APIs,
        unverified MCP tool names, undocumented scripts/imports, ungrounded REST
        endpoints, invented settings/log paths/tables, source-citation drift,
        and native ClickHouse system tables presented as normal HogQL.
        """
        issues: list[dict[str, str]] = []
        allowed_sources = set(allowed_source_ids or [])
        content_lower = content.lower()

        def add(severity: str, issue: str, fix: str) -> None:
            if not any(existing["issue"] == issue for existing in issues):
                issues.append({"severity": severity, "issue": issue, "fix": fix})

        if _INTERNAL_MARKER_RE.search(content):
            add(
                "medium",
                "content leaks an internal context-truncation marker",
                "Remove phrases such as '(evidence truncated)' and write clean reader-facing prose.",
            )

        for citation_line in self._source_citation_lines(content):
            for cited_path in sorted(set(self._extract_file_paths(citation_line))):
                if cited_path not in allowed_sources:
                    add(
                        "high",
                        f"content cites `{cited_path}` as a source instead of a KB source id",
                        "Cite the KB source id from grounding_sources; mention repo files only as files to inspect.",
                    )

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

        for tool_name in sorted(
            set(re.findall(r"(?i)\bMCP\s+`?([A-Za-z0-9_-]+)`?\s+tool\b", content))
        ):
            if not self._is_evidenced(tool_name, evidence_text):
                add(
                    "high",
                    f"content references unsupported MCP tool `{tool_name}`",
                    "Remove the MCP tool reference or replace it with a supported API/table that appears in evidence.",
                )

        for file_path in sorted(set(self._extract_file_paths(content))):
            if file_path in allowed_sources:
                continue
            if not self._is_evidenced(file_path, evidence_text):
                add(
                    "high",
                    f"content references unsupported file path `{file_path}`",
                    "Remove the path or replace it with a file path present in the evidence.",
                )

        for endpoint in sorted(
            set(re.findall(r"(?<!:)`?(/api/[A-Za-z0-9_/@{}<>.-]+/?)[`'\",)]?", content))
        ):
            if not self._is_evidenced(endpoint, evidence_text):
                add(
                    "high",
                    f"content references unsupported endpoint `{endpoint}`",
                    "Use only API paths present in the evidence or describe the request generically.",
                )

        for config_name in sorted(set(_CONFIG_NAME_RE.findall(content))):
            if not self._is_evidenced(config_name, evidence_text):
                add(
                    "medium",
                    f"content references unsupported setting or constant `{config_name}`",
                    "Remove the setting name or replace it with an evidenced configuration key.",
                )

        for match in _SQL_TABLE_RE.finditer(content):
            table_name = match.group(1)
            if "." not in table_name and "_" not in table_name:
                continue
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.start())
            line = content[line_start : line_end if line_end != -1 else len(content)]
            if re.match(
                rf"\s*from\s+{re.escape(table_name)}\s+import\b",
                line,
                flags=re.IGNORECASE,
            ):
                continue
            if not self._is_evidenced(table_name, evidence_text):
                add(
                    "high",
                    f"content references unsupported database table `{table_name}`",
                    "Use only table names present in evidence or describe the storage layer generically.",
                )

        for block in _SQL_BLOCK_RE.findall(content):
            for identifier in sorted(set(_SQL_IDENTIFIER_RE.findall(block))):
                ident_lower = identifier.lower()
                if ident_lower in _SQL_IDENTIFIER_SKIP:
                    continue
                if "_" not in identifier:
                    continue
                if self._is_evidenced(identifier, evidence_text):
                    continue
                add(
                    "medium",
                    f"SQL block references unsupported identifier or column `{identifier}`",
                    "Remove the SQL identifier or replace it with a column/function name that appears verbatim in the evidence.",
                )

        task_lower = task.lower()
        if "web analytics" in task_lower:
            diagnostic_web_heading = re.search(
                r"(?im)^#{2,4}\s+(?=.*web analytics)(?=.*(?:diagnos|check|troubleshoot|freshness|live|path|verify))",
                content,
            )
            if not diagnostic_web_heading:
                add(
                    "medium",
                    "task asks for web analytics coverage but output lacks a dedicated diagnostic web analytics section",
                    "Add an actionable web analytics diagnostic section grounded in the provided web analytics evidence.",
                )
            if (
                any("managing-path-cleaning-rules" in source for source in allowed_sources)
                and "path cleaning" not in content_lower
            ):
                add(
                    "medium",
                    "web analytics path-cleaning evidence is available but output does not cover path-cleaning freshness checks",
                    "Add a path-cleaning freshness-perception check or remove the unused source from the draft.",
                )
            if (
                any("exploring-live-traffic" in source for source in allowed_sources)
                and "live traffic" not in content_lower
            ):
                add(
                    "medium",
                    "web analytics live-traffic evidence is available but output does not cover live-traffic checks",
                    "Add a live-traffic verification step grounded in the web analytics evidence or remove the unused source from the draft.",
                )

        for line in content.splitlines():
            if self._is_dead_end_line(line):
                add(
                    "medium",
                    "content creates a dead-end by requiring source-code inspection where evidence is insufficient",
                    "Remove the required diagnostic step or rewrite it as a limitation; do not force readers to inspect source code to continue.",
                )
                break

        direct_only_tables = (
            "system.replicas",
            "system.parts",
            "system.replication_queue",
            "system.part_log",
        )
        direct_access_terms = ("direct clickhouse", "clickhouse client", "clickhouse cli")
        content_lower = content.lower()
        for table in direct_only_tables:
            if table in content_lower and not any(
                term in content_lower for term in direct_access_terms
            ):
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
                if (
                    module.startswith(("posthog.", "products."))
                    and not self._is_evidenced(f"from {module}", evidence_text)
                    and not self._is_evidenced(f"import {module}", evidence_text)
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
        allowed_source_ids: list[str] | None = None,
    ) -> str:
        issue_lines = "\n".join(
            f"- {issue['severity']}: {issue['issue']} Fix: {issue['fix']}" for issue in issues
        )
        allowed_sources = "\n".join(f"- {source}" for source in allowed_source_ids or [])
        prompt = f"""Rewrite the draft to remove unsupported execution claims.

Hard requirements:
- Use only APIs, endpoints, tables, imports, scripts, and file paths present in the evidence.
- Delete invented MCP helpers and MCP tool names unless they appear in evidence.
- Prefer verified REST endpoints and HogQL tables over wrapper functions.
- Do not invent environment variables, settings, log paths, table schemas, function signatures, or latency numbers.
- Treat native ClickHouse system tables as direct ClickHouse access only, not normal PostHog HogQL.
- If an implementation detail is internal, describe it as a file to inspect rather than a public API to import.
- Do not create a required diagnostic step that only says to inspect source code because evidence is missing.
- If evidence is insufficient for a command/schema/query, state the limitation or remove that step.
- Do not tell readers to inspect, review, or consult source files as a required diagnostic step.
- Do not create limitation-only diagnostic checks. If a check has no concrete grounded action,
  move it to an evidence-limitation note instead of presenting it as a step.
- Cite source documents only by the allowed KB source ids below. Do not cite repository file paths as source labels.
- Return only the revised content.

Allowed KB source ids:
{allowed_sources if allowed_sources else "- No KB source ids were provided."}

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
    def _coverage_requirements(task: str, grounding_source_ids: list[str]) -> str:
        requirements: list[str] = []
        task_lower = task.lower()
        if "web analytics" in task_lower:
            requirements.append(
                "- Web analytics: include a dedicated diagnostic section with concrete checks "
                "grounded in the web analytics/query-runner evidence, not only architecture context."
            )
            if any("managing-path-cleaning-rules" in source for source in grounding_source_ids):
                requirements.append(
                    "- Web analytics path cleaning: include a freshness-perception check for URL/path "
                    "normalization when path-cleaning evidence is available."
                )
            if any("exploring-live-traffic" in source for source in grounding_source_ids):
                requirements.append(
                    "- Web analytics live traffic: include a concrete check for whether the live "
                    "traffic view is receiving events when live-traffic evidence is available."
                )
        if "lazy computation" in task_lower:
            requirements.append(
                "- Lazy computation: include a dedicated diagnostic section for precomputation, "
                "replication, and verified table/settings evidence."
            )
        if any(source.startswith("web-analytics/") for source in grounding_source_ids):
            requirements.append(
                "- If web-analytics KB docs are cited, use them for at least one actionable "
                "verification or troubleshooting step."
            )
        return "\n".join(dict.fromkeys(requirements))

    async def _generate_fast_draft(
        self,
        *,
        prompt: str,
        content_type: str,
    ) -> tuple[str, list[str], list[str]]:
        fast_prompt = f"""{prompt}

## FAST DRAFT MODE
Produce a concise, publishable first draft in one pass.

Hard limits:
- Keep the piece under 1,400 words unless the task explicitly asks for long-form.
- Avoid generic operational filler and vague runbook phrases.
- Do not leak internal notes such as "(evidence truncated)" or "context truncated".
- Do not select SQL columns or identifiers unless those exact names appear in the evidence.
- Do not put angle-bracket placeholders inside runnable code blocks. If a placeholder would
  make code invalid, describe the value in prose instead.
- If evidence does not provide a concrete command, state the limitation instead of inventing one.
- If the task names multiple domains, include a dedicated actionable section for each domain.
- Avoid dead-end steps that only redirect readers to source files. Use the evidence to provide
  a self-contained check, or state that the evidence is insufficient for that check.
- Never tell readers to inspect, review, or consult source files as a required diagnostic step.
- Do not create limitation-only diagnostic checks. If evidence cannot support a concrete action,
  make it an evidence-limitation note rather than a numbered check.
"""
        content = await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=fast_prompt,
            temperature=0.2,
            max_tokens=5000,
            model="sonnet" if content_type in {"tutorial", "blog_post"} else None,
        )
        return content, ["fast grounded draft"], []

    @staticmethod
    def _is_dead_end_line(line: str) -> bool:
        lower = line.lower()
        missing_evidence_markers = (
            "evidence does not",
            "source material does not",
            "does not provide",
            "does not specify",
            "does not verify",
            "not provided",
            "not specified",
            "not verified",
        )
        inspection_terms = ("inspect", "see `", "review `", "consult", "determine")
        return (
            (
                any(marker in lower for marker in missing_evidence_markers)
                and any(term in lower for term in inspection_terms)
            )
            or (
                any(marker in lower for marker in missing_evidence_markers)
                and _FILE_PATH_RE.search(line) is not None
            )
            or (any(marker in lower for marker in missing_evidence_markers) and "mcp tool" in lower)
            or re.search(r"(?i)\bsee\s+`[^`]+`\s+for\s+example", line) is not None
            or re.search(
                r"(?i)\b(?:inspect|review|consult)\s+`?[^`\s]+\.(?:py|md|ts|tsx|js|jsx|xml)`?",
                line,
            )
            is not None
            or ("for deeper investigation" in lower and "consult" in lower and "source" in lower)
        )

    def _remove_dead_end_lines(self, content: str) -> str:
        """Replace source-inspection dead ends with a reader-facing limitation."""
        repaired_lines: list[str] = []
        inserted_note = False
        changed = False
        for line in content.splitlines():
            if not self._is_dead_end_line(line):
                repaired_lines.append(line)
                continue
            changed = True
            if not inserted_note:
                repaired_lines.append(
                    "> Evidence limitation: the current KB evidence does not verify a "
                    "self-contained command, schema, or example for this step."
                )
                inserted_note = True
        if not changed:
            return content
        return re.sub(r"\n{3,}", "\n\n", "\n".join(repaired_lines)).strip()

    def _remove_unsupported_internal_imports(self, content: str, evidence_text: str) -> str:
        """Replace runnable internal imports that are not verified by evidence."""

        def unsupported_modules(text: str) -> list[str]:
            modules: list[str] = []
            for match in _INTERNAL_IMPORT_RE.finditer(text):
                module = match.group(1) or match.group(2) or ""
                if module and not (
                    self._is_evidenced(f"from {module}", evidence_text)
                    or self._is_evidenced(f"import {module}", evidence_text)
                ):
                    modules.append(module)
            return sorted(set(modules))

        def replace_block(match: re.Match[str]) -> str:
            modules = unsupported_modules(match.group(2))
            if not modules:
                return match.group(0)
            names = ", ".join(f"`{module}`" for module in modules[:4])
            return (
                "> Evidence limitation: the current KB evidence does not verify "
                f"a runnable internal import example for {names}."
            )

        repaired = _CODE_BLOCK_RE.sub(replace_block, content)
        if repaired == content:
            modules = unsupported_modules(content)
            if modules:
                names = ", ".join(f"`{module}`" for module in modules[:4])
                repaired = _INTERNAL_IMPORT_RE.sub(
                    "> Evidence limitation: the current KB evidence does not verify "
                    f"a runnable internal import example for {names}.",
                    content,
                )
        return re.sub(r"\n{3,}", "\n\n", repaired).strip()

    @staticmethod
    def _remove_invalid_code_blocks(content: str, errors: list[Any]) -> str:
        """Replace fenced code blocks that failed syntax validation with a limitation note."""
        invalid_blocks = {
            (str(error.block.language).lower(), error.block.code.strip()) for error in errors
        }
        if not invalid_blocks:
            return content

        def replace(match: re.Match[str]) -> str:
            language = match.group(1).lower().strip()
            code = match.group(2).strip()
            if (language, code) not in invalid_blocks:
                return match.group(0)
            return (
                "> Evidence limitation: this runnable code example was removed because "
                "it failed syntax validation. Use the surrounding verified data model "
                "and source notes instead of copying an invalid snippet."
            )

        return re.sub(r"\n{3,}", "\n\n", _CODE_BLOCK_RE.sub(replace, content)).strip()

    @staticmethod
    def _demote_limitation_only_checks(content: str) -> str:
        """Avoid numbered diagnostic checks that only contain limitations."""

        def replace(match: re.Match[str]) -> str:
            title = match.group(1).strip()
            body = match.group(2).strip()
            if len(body) < 20:
                return ""
            has_limitation = "Evidence limitation" in body or "Evidence note" in body
            if has_limitation and "```" not in body:
                return (
                    f"### Evidence limitation: {title}\n\n"
                    "The current KB evidence does not verify a self-contained command, "
                    "schema, or example for this diagnostic.\n\n"
                )
            return match.group(0)

        return re.sub(r"\n{3,}", "\n\n", _CHECK_SECTION_RE.sub(replace, content)).strip()

    def _remove_unsupported_sql_blocks(self, content: str, evidence_text: str) -> str:
        """Replace SQL blocks containing unevidenced identifiers with prose.

        This keeps a draft publishable without shipping copy-paste SQL that
        selected columns or placeholders the evidence did not verify.
        """

        def replace(match: re.Match[str]) -> str:
            block = match.group(1)
            unsupported = []
            for identifier in sorted(set(_SQL_IDENTIFIER_RE.findall(block))):
                ident_lower = identifier.lower()
                if ident_lower in _SQL_IDENTIFIER_SKIP or "_" not in identifier:
                    continue
                if not self._is_evidenced(identifier, evidence_text):
                    unsupported.append(identifier)
            if not unsupported:
                return match.group(0)
            names = ", ".join(f"`{name}`" for name in unsupported[:6])
            return (
                "> Evidence note: the source material does not verify a safe "
                f"copy-paste SQL query for this check because {names} "
                "did not appear in the provided evidence. Treat this as a "
                "current evidence limitation rather than a runnable query."
            )

        return _SQL_BLOCK_RE.sub(replace, content)

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
        editorial_mode: str = "pipeline",
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
        raw_grounding_docs = self.search_knowledge_base(
            search_query,
            max_results=5,
            content_truncate=5000,
        )
        task_lower = task.lower()
        grounding_docs = [
            doc
            for doc in raw_grounding_docs
            if float(doc.get("relevance", 0) or 0) > 0
            or (
                self._requires_evidence(task_lower, ("file path", "source code"))
                and self._contains_file_path(f"{doc.get('content', '')}\n{doc.get('source', '')}")
            )
        ]
        per_doc_budget = max(1200, 11500 // max(len(grounding_docs), 1))
        grounding_context = "\n\n".join(
            f"[Source: {doc['source']}]\n{str(doc['content'])[:per_doc_budget]}"
            for doc in grounding_docs
        )[:12000]
        grounding_source_ids = [doc["source"] for doc in grounding_docs]
        citation_source_section = "\n".join(f"- {source}" for source in grounding_source_ids)
        coverage_section = self._coverage_requirements(task, grounding_source_ids)

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

## Allowed Citation Source IDs
{citation_source_section if citation_source_section else "No knowledge base source ids available."}

## Repository Architecture (from source code analysis)
{arch_doc if arch_doc else "No architecture analysis available."}
{f"Summary: {dex_summary}" if dex_summary else ""}

## Source Evidence
{source_section if source_section else "No source file evidence available."}

## Official Documentation Reference
{official_docs if official_docs else "No official docs fetched."}

## Content Brief
{brief_section if brief_section else "No explicit content brief available."}

## Required Coverage
{coverage_section if coverage_section else "No additional domain coverage requirements."}

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
3. Reference REAL file paths and directory structures only when they appear in the
   evidence above. Treat repo paths as files to inspect, not source-citation labels.
4. When showing code examples, base them on actual patterns from the source code.
   Prefer documented REST endpoints, HogQL queries, and existing file paths over
   invented helper functions. Never invent MCP tool names or wrapper functions.
5. Address a community pain point only when one is present in the Community Context.
6. Reference GitHub issue titles/numbers only when real issues are present in the
   Community Context. If none are present, omit issue references entirely.
7. Structure: Prerequisites → Step-by-step → Verification → Troubleshooting → Next Steps.
8. Cite only the allowed KB source ids listed above. Use those ids exactly; do not
   cite repository file paths as standalone sources.
9. Do NOT hallucinate URLs, endpoints, or configuration options that aren't in the context.
10. Do NOT repeat topics from the Content History section — pick a fresh angle or go deeper.
11. If a theme keeps recurring across weeks, produce advanced/deep-dive content instead of intro-level.
12. If you mention a native ClickHouse system table such as system.replicas,
    system.parts, system.replication_queue, or system.part_log, clearly mark it
    as direct ClickHouse access only, not a PostHog HogQL table.
13. Do NOT invent log file paths, environment variables, Django settings, table schemas,
    function signatures, or latency/capacity numbers. If the evidence does not specify
    them, say the evidence does not specify them.
14. For maintainer diagnostics, attach a KB source id to each concrete path, table,
    setting, endpoint, or command claim so the reader can verify it.
15. Never output internal context-management notes such as "evidence truncated",
    "context truncated", or similar meta commentary.
16. If SQL examples include selected columns, every non-generic column or identifier
    must appear verbatim in the evidence. Otherwise state the evidence limitation in prose.
17. Satisfy every Required Coverage bullet with a concrete section in the draft.
18. Do not tell readers to inspect, review, or consult source files as a required diagnostic
    step. If the evidence is missing details, state that limitation without turning it into
    a required step.
19. Do not create limitation-only diagnostic checks. If a check has no concrete grounded action,
    move it to an evidence-limitation note instead of presenting it as a step.
20. Do not put angle-bracket placeholders inside runnable code blocks. If a placeholder would
    make code invalid, describe the value in prose instead.
"""

        base_result = {
            "agent": "kai",
            "task": task,
            "grounding_sources": grounding_source_ids,
            "pain_points_addressed": [pp["title"] for pp in pain_points[:3]],
            "real_issues_referenced": [i["number"] for i in real_issues[:5]],
            "status": "generated",
            "editorial_mode": editorial_mode,
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
                mode = editorial_mode.strip().lower()
                if mode in {"fast", "direct"}:
                    content, strengths, issues = await self._generate_fast_draft(
                        prompt=prompt,
                        content_type=content_type,
                    )
                else:
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

                evidence_text = "\n\n".join(
                    [
                        grounding_context,
                        arch_doc,
                        dex_summary,
                        source_section,
                        official_docs,
                        brief_section,
                        coverage_section,
                    ]
                )
                content = self._normalize_unsupported_placeholders(
                    self._sanitize_internal_markers(content)
                )
                content = self._demote_limitation_only_checks(content)
                base_result["content"] = content
                grounding_issues = self._grounded_output_issues(
                    content,
                    evidence_text,
                    allowed_source_ids=grounding_source_ids,
                    task=task,
                )
                if grounding_issues:
                    rewritten = await self._rewrite_ungrounded_content(
                        content=content,
                        issues=grounding_issues,
                        evidence_text=evidence_text,
                        content_type=content_type,
                        allowed_source_ids=grounding_source_ids,
                    )
                    rewritten = self._normalize_unsupported_placeholders(
                        self._sanitize_internal_markers(rewritten)
                    )
                    rewritten = self._demote_limitation_only_checks(rewritten)
                    rewritten_issues = self._grounded_output_issues(
                        rewritten,
                        evidence_text,
                        allowed_source_ids=grounding_source_ids,
                        task=task,
                    )
                    deterministic_repair = False
                    if rewritten_issues:
                        repaired = self._remove_unsupported_sql_blocks(rewritten, evidence_text)
                        repaired = self._remove_unsupported_internal_imports(
                            repaired,
                            evidence_text,
                        )
                        repaired = self._remove_dead_end_lines(repaired)
                        repaired = self._demote_limitation_only_checks(repaired)
                        if repaired != rewritten:
                            deterministic_repair = True
                            rewritten = self._normalize_unsupported_placeholders(
                                self._sanitize_internal_markers(repaired)
                            )
                            rewritten_issues = self._grounded_output_issues(
                                rewritten,
                                evidence_text,
                                allowed_source_ids=grounding_source_ids,
                                task=task,
                            )
                    base_result["content"] = rewritten
                    base_result["grounding_validation"] = {
                        "rewritten": True,
                        "deterministic_repair": deterministic_repair,
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

                code_repair = False
                report = self.code_validator.validate_content(base_result.get("content", ""))
                if not report.all_passed:
                    repaired = self._remove_invalid_code_blocks(
                        base_result.get("content", ""),
                        report.errors,
                    )
                    if repaired != base_result.get("content", ""):
                        code_repair = True
                        base_result["content"] = repaired
                        report = self.code_validator.validate_content(repaired)
                code_payload = self._code_validation_payload(report)
                code_payload["deterministic_repair"] = code_repair
                base_result["code_validation"] = code_payload
                if not report.all_passed:
                    logger.warning(
                        f"Code validation: {report.failed}/{report.validated} "
                        f"blocks failed syntax checks"
                    )
                    base_result["status"] = "blocked_by_code_validation"
                    base_result["content"] = ""
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
