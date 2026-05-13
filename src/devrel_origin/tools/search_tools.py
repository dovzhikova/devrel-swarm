"""
Search Tools — Web search, content retrieval, and documentation lookup.

Provides tools for grounding agent outputs in real-world data:
- OpenClaw documentation search
- General web search (via Firecrawl API)
- URL content extraction
"""

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

PRODUCT_DOCS_BASE = os.getenv("PRODUCT_URL", "https://openclaw.ai")
GITHUB_REPO = os.getenv("GITHUB_REPO", "openclaw/openclaw")
GITMCP_BASE = f"https://gitmcp.io/{GITHUB_REPO}"
FIRECRAWL_API = "https://api.firecrawl.dev/v1"
BRAVE_API = "https://api.search.brave.com/res/v1"
API_TIMEOUT = 20.0


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str
    source: str  # "devrel_ai_agents_docs", "web", "discourse"
    relevance_score: float = 0.0


@dataclass
class DocSection:
    """A section from OpenClaw documentation."""

    title: str
    url: str
    content: str
    breadcrumb: list[str]


class SearchTools:
    """
    Search and retrieval tools for content grounding.

    Supports:
    - OpenClaw documentation search
    - Firecrawl web search API (primary), Brave Search API (fallback)
    - OpenClaw community forum search
    - URL content extraction (Firecrawl scrape with direct HTTP fallback)

    Usage::

        search = SearchTools(firecrawl_api_key="fc-...", brave_api_key="BSA...")
        results = await search.search_devrel_ai_agents_docs("agent orchestration")
        web_results = await search.web_search("OpenClaw vs alternatives")
    """

    def __init__(self, firecrawl_api_key: str = "", brave_api_key: str = ""):
        self.firecrawl_api_key = firecrawl_api_key
        self.brave_api_key = brave_api_key
        self._client = httpx.AsyncClient(timeout=API_TIMEOUT)

    async def close(self) -> None:
        await self._client.aclose()

    # -- OpenClaw Docs Search --------------------------------------

    async def search_devrel_ai_agents_docs(self, query: str, limit: int = 10) -> list[SearchResult]:
        """
        Search product documentation.

        Falls back to site-scoped web search if direct API unavailable.
        """
        try:
            resp = await self._client.get(
                f"{PRODUCT_DOCS_BASE}/api/search",
                params={"q": query, "limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    SearchResult(
                        title=hit.get("title", ""),
                        url=f"{PRODUCT_DOCS_BASE}{hit.get('url', '')}",
                        snippet=hit.get("excerpt", ""),
                        source="product_docs",
                        relevance_score=hit.get("score", 0),
                    )
                    for hit in data.get("results", [])[:limit]
                ]
        except Exception as exc:
            logger.warning(f"Product docs search failed: {exc}")

        # Fallback: site-scoped web search
        docs_domain = PRODUCT_DOCS_BASE.replace("https://", "").replace("http://", "")
        return await self.web_search(f"site:{docs_domain} {query}", limit=limit)

    # -- Official Docs (GitMCP) -------------------------------------------

    async def fetch_official_docs(self, topic: str, max_chars: int = 8000) -> str:
        """
        Fetch official OpenClaw documentation via GitMCP.

        Queries https://gitmcp.io/openclaw/openclaw for the given topic
        to ensure content agents produce accurate, up-to-date information.
        Returns raw documentation text for cross-referencing.
        """
        url = f"{GITMCP_BASE}"
        try:
            # Fetch the repo README / docs index first
            content = await self.fetch_url_content(url, max_chars=max_chars)
            if content:
                logger.info(f"Fetched official docs from GitMCP ({len(content)} chars)")
                return content
        except Exception as exc:
            logger.warning(f"GitMCP fetch failed: {exc}")

        # Fallback: search official docs site
        logger.info("Falling back to OpenClaw docs search for official reference")
        results = await self.search_devrel_ai_agents_docs(topic, limit=5)
        if results:
            sections = []
            for r in results[:3]:
                section_content = await self.fetch_url_content(r.url, max_chars=2000)
                if section_content:
                    sections.append(f"## {r.title}\nSource: {r.url}\n\n{section_content}")
            return "\n\n---\n\n".join(sections)

        return ""

    # -- Community Search -------------------------------------------------

    async def search_discourse(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search product community forum (Discourse)."""
        community_url = os.getenv("COMMUNITY_URL", "")
        if not community_url:
            return []
        try:
            resp = await self._client.get(
                f"{community_url}/search.json",
                params={"q": query},
            )
            if resp.status_code == 200:
                data = resp.json()
                topics = data.get("topics", [])
                return [
                    SearchResult(
                        title=t.get("title", ""),
                        url=f"{community_url}/t/{t.get('slug', '')}/{t.get('id', '')}",
                        snippet=t.get("excerpt", ""),
                        source="discourse",
                    )
                    for t in topics[:limit]
                ]
        except Exception as exc:
            logger.warning(f"Discourse search failed: {exc}")

        return []

    # -- Web Search -------------------------------------------------------

    async def web_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """
        General web search. Tries Firecrawl first, falls back to Brave Search.

        Requires at least one API key (Firecrawl or Brave).
        """
        # Try Firecrawl first
        if self.firecrawl_api_key:
            results = await self._firecrawl_search(query, limit)
            if results:
                return results
            logger.info("Firecrawl returned no results, trying Brave fallback")

        # Fallback to Brave
        if self.brave_api_key:
            return await self._brave_search(query, limit)

        logger.warning("No search API keys configured — web search unavailable")
        return []

    async def _firecrawl_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search via Firecrawl API."""
        try:
            resp = await self._client.post(
                f"{FIRECRAWL_API}/search",
                headers={
                    "Authorization": f"Bearer {self.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("data", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("description", ""),
                        source="web",
                    )
                )
            return results[:limit]

        except Exception as exc:
            logger.warning(f"Firecrawl web search failed: {exc}")
            return []

    async def _brave_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search via Brave Search API (fallback)."""
        try:
            resp = await self._client.get(
                f"{BRAVE_API}/web/search",
                headers={
                    "X-Subscription-Token": self.brave_api_key,
                    "Accept": "application/json",
                },
                params={"q": query, "count": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("web", {}).get("results", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("description", ""),
                        source="web",
                    )
                )
            return results[:limit]

        except Exception as exc:
            logger.warning(f"Brave web search failed: {exc}")
            return []

    # -- URL Content Extraction -------------------------------------------

    async def fetch_url_content(self, url: str, max_chars: int = 10_000) -> str:
        """
        Fetch and extract text content from a URL.

        When a Firecrawl API key is available, uses the Firecrawl scrape endpoint
        for cleaner markdown output. Falls back to direct HTTP fetch with HTML
        stripping otherwise.
        """
        if self.firecrawl_api_key:
            try:
                resp = await self._client.post(
                    f"{FIRECRAWL_API}/scrape",
                    headers={
                        "Authorization": f"Bearer {self.firecrawl_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": url, "formats": ["markdown"]},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("success"):
                    text = data.get("data", {}).get("markdown", "")
                    return text[:max_chars]
            except Exception as exc:
                logger.warning(
                    f"Firecrawl scrape failed for {url}: {exc}, falling back to direct fetch"
                )

        # Fallback: direct HTTP fetch with HTML stripping
        try:
            resp = await self._client.get(
                url,
                follow_redirects=True,
                headers={"User-Agent": "DevRelAIAgents/1.0"},
            )
            resp.raise_for_status()
            text = resp.text

            # Crude HTML stripping (production would use readability)
            import re

            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            return text[:max_chars]

        except Exception as exc:
            logger.warning(f"URL fetch failed for {url}: {exc}")
            return ""

    # -- Knowledge Base Helpers -------------------------------------------

    @staticmethod
    def rank_results(
        results: list[SearchResult],
        query: str,
    ) -> list[SearchResult]:
        """
        Re-rank search results by keyword overlap with query.

        Simple TF-based scoring — production would use embeddings.
        """
        query_terms = set(query.lower().split())
        for result in results:
            text = f"{result.title} {result.snippet}".lower()
            overlap = sum(1 for term in query_terms if term in text)
            result.relevance_score = overlap / max(len(query_terms), 1)

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results
