"""Tests for search tools module.

Uses respx to mock httpx calls — never hits real APIs.
"""

import httpx
import respx

from devrel_swarm.tools.search_tools import SearchResult, SearchTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(title: str, snippet: str = "", source: str = "web") -> SearchResult:
    return SearchResult(
        title=title,
        url=f"https://example.com/{title}",
        snippet=snippet,
        source=source,
    )


# ---------------------------------------------------------------------------
# TestSearchResultDataclass
# ---------------------------------------------------------------------------


class TestSearchResultDataclass:
    def test_create_search_result(self):
        result = SearchResult(
            title="Test Article",
            url="https://example.com/article",
            snippet="This is a test article about testing.",
            source="example.com",
            relevance_score=0.95,
        )
        assert result.title == "Test Article"
        assert result.url == "https://example.com/article"
        assert result.relevance_score == 0.95

    def test_default_relevance_score(self):
        result = SearchResult(
            title="No Score",
            url="https://example.com",
            snippet="Content",
            source="web",
        )
        assert result.relevance_score == 0.0


# ---------------------------------------------------------------------------
# TestSearchPosthogDocs
# ---------------------------------------------------------------------------


class TestSearchDevrelDocs:
    async def test_docs_search_success(self):
        """Mock OpenClaw API returning results — verify parsed into SearchResult."""
        payload = {
            "results": [
                {
                    "title": "Feature Flags Guide",
                    "url": "/docs/feature-flags",
                    "excerpt": "Learn about feature flags",
                    "score": 0.9,
                },
                {
                    "title": "Analytics Overview",
                    "url": "/docs/analytics",
                    "excerpt": "Track events",
                    "score": 0.7,
                },
            ]
        }
        with respx.mock:
            respx.get("https://example.com/api/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            search = SearchTools(firecrawl_api_key="test-key")
            try:
                results = await search.search_devrel_docs("feature flags")
            finally:
                await search.close()

        assert len(results) == 2
        assert results[0].title == "Feature Flags Guide"
        assert results[0].url == "https://example.com/docs/feature-flags"
        assert results[0].snippet == "Learn about feature flags"
        assert results[0].source == "devrel_docs"
        assert results[0].relevance_score == 0.9

    async def test_docs_search_fallback_to_web(self):
        """When OpenClaw API returns 500, falls back to web_search with site: prefix."""
        web_payload = {
            "success": True,
            "data": [
                {
                    "title": "Feature flags on example.com",
                    "url": "https://example.com/docs/ff",
                    "description": "OpenClaw feature flags",
                },
            ],
        }
        with respx.mock:
            respx.get("https://example.com/api/search").mock(return_value=httpx.Response(500))
            firecrawl_route = respx.post("https://api.firecrawl.dev/v1/search").mock(
                return_value=httpx.Response(200, json=web_payload)
            )
            search = SearchTools(firecrawl_api_key="test-key")
            try:
                results = await search.search_devrel_docs("feature flags")
            finally:
                await search.close()

            assert len(results) == 1
            assert results[0].source == "web"
            # Verify the Firecrawl search was called
            assert firecrawl_route.called

    async def test_docs_search_respects_limit(self):
        """Verify only `limit` results are returned even if API returns more."""
        payload = {
            "results": [
                {
                    "title": f"Doc {i}",
                    "url": f"/docs/doc-{i}",
                    "excerpt": "",
                    "score": 0.5,
                }
                for i in range(20)
            ]
        }
        with respx.mock:
            respx.get("https://example.com/api/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            search = SearchTools(firecrawl_api_key="test-key")
            try:
                results = await search.search_devrel_docs("analytics", limit=3)
            finally:
                await search.close()

        assert len(results) == 3


# ---------------------------------------------------------------------------
# TestSearchDiscourse
# ---------------------------------------------------------------------------


class TestSearchDiscourse:
    async def test_discourse_success(self):
        """Mock discourse endpoint with topics array — verify parsed."""
        payload = {
            "topics": [
                {
                    "title": "How to use feature flags?",
                    "slug": "how-to-use-feature-flags",
                    "id": 123,
                    "excerpt": "I want to know...",
                },
                {
                    "title": "Error with analytics",
                    "slug": "error-with-analytics",
                    "id": 456,
                    "excerpt": "Getting an error",
                },
            ]
        }
        with respx.mock:
            respx.get("https://community.example.com/search.json").mock(
                return_value=httpx.Response(200, json=payload)
            )
            search = SearchTools()
            try:
                results = await search.search_discourse("feature flags")
            finally:
                await search.close()

        assert len(results) == 2
        assert results[0].title == "How to use feature flags?"
        assert results[0].url == "https://community.example.com/t/how-to-use-feature-flags/123"
        assert results[0].source == "discourse"
        assert results[0].snippet == "I want to know..."

    async def test_discourse_failure_returns_empty(self):
        """500 response from discourse should return empty list."""
        with respx.mock:
            respx.get("https://community.example.com/search.json").mock(
                return_value=httpx.Response(500)
            )
            search = SearchTools()
            try:
                results = await search.search_discourse("feature flags")
            finally:
                await search.close()

        assert results == []


# ---------------------------------------------------------------------------
# TestWebSearch
# ---------------------------------------------------------------------------


class TestWebSearch:
    async def test_web_search_success(self):
        """Mock Firecrawl API — verify results are correctly parsed."""
        payload = {
            "success": True,
            "data": [
                {
                    "title": "PostHog vs Amplitude",
                    "url": "https://example.com/comparison",
                    "description": "A detailed comparison",
                },
                {
                    "title": "Best analytics tools",
                    "url": "https://example.com/analytics",
                    "description": "Top tools list",
                },
            ],
        }
        with respx.mock:
            respx.post("https://api.firecrawl.dev/v1/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            search = SearchTools(firecrawl_api_key="test-firecrawl-key")
            try:
                results = await search.web_search("PostHog vs Amplitude")
            finally:
                await search.close()

        assert len(results) == 2
        assert results[0].title == "PostHog vs Amplitude"
        assert results[0].url == "https://example.com/comparison"
        assert results[0].snippet == "A detailed comparison"
        assert results[0].source == "web"

    async def test_web_search_no_api_keys(self):
        """No Firecrawl or Brave key → return empty list immediately."""
        search = SearchTools()
        try:
            results = await search.web_search("anything")
        finally:
            await search.close()

        assert results == []

    async def test_web_search_firecrawl_failure_falls_back_to_brave(self):
        """Firecrawl 500 → falls back to Brave and returns results."""
        brave_payload = {
            "web": {
                "results": [
                    {
                        "title": "Brave fallback result",
                        "url": "https://example.com/brave",
                        "description": "Found via Brave",
                    },
                ],
            },
        }
        with respx.mock:
            respx.post("https://api.firecrawl.dev/v1/search").mock(return_value=httpx.Response(500))
            brave_route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
                return_value=httpx.Response(200, json=brave_payload)
            )
            search = SearchTools(firecrawl_api_key="fc-key", brave_api_key="brave-key")
            try:
                results = await search.web_search("something")
            finally:
                await search.close()

        assert brave_route.called
        assert len(results) == 1
        assert results[0].title == "Brave fallback result"

    async def test_web_search_brave_only(self):
        """Only Brave key set → uses Brave directly."""
        brave_payload = {
            "web": {
                "results": [
                    {
                        "title": "Brave result",
                        "url": "https://example.com/brave",
                        "description": "From Brave",
                    },
                ],
            },
        }
        with respx.mock:
            brave_route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
                return_value=httpx.Response(200, json=brave_payload)
            )
            search = SearchTools(brave_api_key="brave-key")
            try:
                results = await search.web_search("test")
            finally:
                await search.close()

        assert brave_route.called
        assert len(results) == 1
        assert results[0].title == "Brave result"

    async def test_web_search_firecrawl_empty_falls_back_to_brave(self):
        """Firecrawl returns empty data → falls back to Brave."""
        firecrawl_payload = {"success": True, "data": []}
        brave_payload = {
            "web": {
                "results": [
                    {
                        "title": "Brave result",
                        "url": "https://example.com/b",
                        "description": "Fallback",
                    },
                ],
            },
        }
        with respx.mock:
            respx.post("https://api.firecrawl.dev/v1/search").mock(
                return_value=httpx.Response(200, json=firecrawl_payload)
            )
            brave_route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
                return_value=httpx.Response(200, json=brave_payload)
            )
            search = SearchTools(firecrawl_api_key="fc-key", brave_api_key="brave-key")
            try:
                results = await search.web_search("test")
            finally:
                await search.close()

        assert brave_route.called
        assert len(results) == 1

    async def test_web_search_sends_correct_headers(self):
        """Verify Authorization Bearer header is sent with the Firecrawl key."""
        payload = {"success": True, "data": []}
        with respx.mock:
            route = respx.post("https://api.firecrawl.dev/v1/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            search = SearchTools(firecrawl_api_key="my-firecrawl-key-123")
            try:
                await search.web_search("test query")
            finally:
                await search.close()

        assert route.called
        sent_headers = route.calls.last.request.headers
        assert sent_headers["authorization"] == "Bearer my-firecrawl-key-123"


# ---------------------------------------------------------------------------
# TestFetchUrlContent
# ---------------------------------------------------------------------------


class TestFetchUrlContent:
    async def test_fetch_success(self):
        """Mock a URL returning HTML — verify HTML tags are stripped."""
        html = "<html><body><h1>Hello World</h1><p>This is content.</p></body></html>"
        with respx.mock:
            respx.get("https://example.com/page").mock(return_value=httpx.Response(200, text=html))
            search = SearchTools()
            try:
                text = await search.fetch_url_content("https://example.com/page")
            finally:
                await search.close()

        assert "<html>" not in text
        assert "<body>" not in text
        assert "Hello World" in text
        assert "This is content." in text

    async def test_fetch_truncates(self):
        """max_chars=50 should truncate output to 50 characters."""
        long_text = "A" * 200
        html = f"<p>{long_text}</p>"
        with respx.mock:
            respx.get("https://example.com/long").mock(return_value=httpx.Response(200, text=html))
            search = SearchTools()
            try:
                text = await search.fetch_url_content("https://example.com/long", max_chars=50)
            finally:
                await search.close()

        assert len(text) <= 50

    async def test_fetch_failure_returns_empty(self):
        """Connection error or HTTP error → return empty string."""
        with respx.mock:
            respx.get("https://example.com/broken").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            search = SearchTools()
            try:
                text = await search.fetch_url_content("https://example.com/broken")
            finally:
                await search.close()

        assert text == ""

    async def test_fetch_strips_scripts(self):
        """Script and style tags (with their content) should be removed."""
        html = (
            "<html><head>"
            "<script>alert('xss');</script>"
            "<style>body { color: red; }</style>"
            "</head><body><p>Real content here.</p></body></html>"
        )
        with respx.mock:
            respx.get("https://example.com/scripts").mock(
                return_value=httpx.Response(200, text=html)
            )
            search = SearchTools()
            try:
                text = await search.fetch_url_content("https://example.com/scripts")
            finally:
                await search.close()

        assert "alert" not in text
        assert "color: red" not in text
        assert "Real content here." in text

    async def test_fetch_uses_firecrawl_scrape_when_key_available(self):
        """When firecrawl_api_key is set, use Firecrawl scrape endpoint."""
        scrape_payload = {
            "success": True,
            "data": {
                "markdown": "# Hello World\n\nThis is scraped content.",
                "metadata": {"title": "Hello World"},
            },
        }
        with respx.mock:
            route = respx.post("https://api.firecrawl.dev/v1/scrape").mock(
                return_value=httpx.Response(200, json=scrape_payload)
            )
            search = SearchTools(firecrawl_api_key="fc-test-key")
            try:
                text = await search.fetch_url_content("https://example.com/page")
            finally:
                await search.close()

        assert route.called
        assert "Hello World" in text
        assert "This is scraped content." in text

    async def test_fetch_falls_back_to_direct_on_firecrawl_failure(self):
        """When Firecrawl scrape fails, fall back to direct HTTP fetch."""
        html = "<html><body><p>Fallback content.</p></body></html>"
        with respx.mock:
            respx.post("https://api.firecrawl.dev/v1/scrape").mock(return_value=httpx.Response(500))
            respx.get("https://example.com/page").mock(return_value=httpx.Response(200, text=html))
            search = SearchTools(firecrawl_api_key="fc-test-key")
            try:
                text = await search.fetch_url_content("https://example.com/page")
            finally:
                await search.close()

        assert "Fallback content." in text


# ---------------------------------------------------------------------------
# TestRankResults
# ---------------------------------------------------------------------------


class TestRankResults:
    def test_rank_results_ordering(self):
        """Best keyword match should appear first."""
        results = [
            make_result("Ruby Guide", snippet="Ruby on Rails framework tutorial"),
            make_result(
                "Python Testing Deep Dive",
                snippet="pytest and testing in Python",
            ),
            make_result(
                "Python Intro",
                snippet="Getting started with Python programming",
            ),
        ]
        ranked = SearchTools.rank_results(results, "python testing")

        assert ranked[0].title == "Python Testing Deep Dive"

    def test_rank_results_empty(self):
        """Ranking an empty list should return an empty list."""
        result = SearchTools.rank_results([], "python")
        assert result == []

    def test_rank_results_scores(self):
        """relevance_score should be set as overlap / len(query_terms)."""
        results = [
            make_result(
                "Feature Flags Overview",
                snippet="feature flags documentation",
            ),
        ]
        ranked = SearchTools.rank_results(results, "feature flags")

        # query_terms = {"feature", "flags"} — both appear in title + snippet
        # overlap = 2, len(query_terms) = 2 → score = 1.0
        assert ranked[0].relevance_score == 1.0

    def test_rank_results_partial_match_score(self):
        """Partial query match should produce a score between 0 and 1."""
        results = [
            make_result(
                "Feature Documentation",
                snippet="learn about features here",
            ),
        ]
        ranked = SearchTools.rank_results(results, "feature flags")

        # query_terms = {"feature", "flags"} — only "feature" appears
        # overlap = 1, len = 2 → score = 0.5
        assert ranked[0].relevance_score == 0.5

    def test_rank_results_no_match_score_zero(self):
        """No keyword overlap should yield relevance_score == 0.0."""
        results = [
            make_result("Cooking Recipes", snippet="How to make pasta"),
        ]
        ranked = SearchTools.rank_results(results, "feature flags")
        assert ranked[0].relevance_score == 0.0


# ---------------------------------------------------------------------------
# TestSearchToolsInit (kept from original)
# ---------------------------------------------------------------------------


class TestSearchToolsInit:
    def test_init_without_api_keys(self):
        search = SearchTools()
        assert search.firecrawl_api_key == ""
        assert search.brave_api_key == ""

    def test_init_with_firecrawl_key(self):
        search = SearchTools(firecrawl_api_key="fc_test_key")
        assert search.firecrawl_api_key == "fc_test_key"
        assert search.brave_api_key == ""

    def test_init_with_both_keys(self):
        search = SearchTools(firecrawl_api_key="fc-key", brave_api_key="brave-key")
        assert search.firecrawl_api_key == "fc-key"
        assert search.brave_api_key == "brave-key"


# ---------------------------------------------------------------------------
# TestFetchOfficialDocs
# ---------------------------------------------------------------------------


class TestFetchOfficialDocs:
    async def test_fetch_official_docs_success(self):
        """GitMCP returns content — verify it's returned as-is."""
        html = "<html><body><p>Official OpenClaw docs content here.</p></body></html>"
        with respx.mock:
            respx.get("https://gitmcp.io/openclaw/openclaw").mock(
                return_value=httpx.Response(200, text=html)
            )
            search = SearchTools()
            try:
                result = await search.fetch_official_docs("feature flags")
            finally:
                await search.close()

        assert "Official OpenClaw docs content" in result

    async def test_fetch_official_docs_gitmcp_failure_falls_back(self):
        """GitMCP fails — falls back to OpenClaw docs search."""
        docs_payload = {
            "results": [
                {
                    "title": "Feature Flags",
                    "url": "/docs/feature-flags",
                    "excerpt": "Guide to flags",
                    "score": 0.9,
                },
            ]
        }
        section_html = "<html><body><p>Feature flag docs section.</p></body></html>"
        with respx.mock:
            respx.get("https://gitmcp.io/openclaw/openclaw").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            respx.get("https://example.com/api/search").mock(
                return_value=httpx.Response(200, json=docs_payload)
            )
            respx.get("https://example.com/docs/feature-flags").mock(
                return_value=httpx.Response(200, text=section_html)
            )
            search = SearchTools()
            try:
                result = await search.fetch_official_docs("feature flags")
            finally:
                await search.close()

        assert "Feature Flags" in result
        assert "Feature flag docs section" in result

    async def test_fetch_official_docs_all_failures_returns_empty(self):
        """Both GitMCP and OpenClaw docs fail — return empty string."""
        with respx.mock:
            respx.get("https://gitmcp.io/openclaw/openclaw").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            respx.get("https://example.com/api/search").mock(return_value=httpx.Response(500))
            # Fallback web search also returns nothing
            search = SearchTools()
            try:
                result = await search.fetch_official_docs("feature flags")
            finally:
                await search.close()

        assert result == ""
