"""Tests for PostHog API client module."""

from devrel_swarm.tools.api_client import InsightQuery, PostHogClient


class TestInsightQuerySerialization:
    """Test InsightQuery.to_dict() serialization."""

    def test_basic_serialization(self):
        query = InsightQuery(
            insight="TRENDS",
            events=[{"id": "$pageview"}],
            date_from="-7d",
        )
        data = query.to_dict()
        assert data["insight"] == "TRENDS"
        assert data["events"] == [{"id": "$pageview"}]
        assert data["date_from"] == "-7d"

    def test_serialization_with_breakdown(self):
        query = InsightQuery(
            insight="TRENDS",
            events=[{"id": "user_signup"}],
            breakdown="country",
        )
        data = query.to_dict()
        assert data["breakdown"] == "country"
        assert data["breakdown_type"] == "event"

    def test_serialization_with_date_range(self):
        query = InsightQuery(
            events=[{"id": "api_call"}],
            date_from="2026-03-01",
            date_to="2026-03-13",
        )
        data = query.to_dict()
        assert data["date_from"] == "2026-03-01"
        assert data["date_to"] == "2026-03-13"


class TestPostHogClientUrlBuilding:
    """Test _url() project-scoped URL building."""

    def test_url_with_project_id(self):
        client = PostHogClient(api_key="test_key", project_id="12345")
        url = client._url("/insights/")
        assert url == "/api/projects/12345/insights/"

    def test_url_without_project_id(self):
        client = PostHogClient(api_key="test_key", project_id="")
        url = client._url("/insights/")
        assert url == "/api/insights/"

    def test_url_for_events(self):
        client = PostHogClient(api_key="test_key", project_id="99999")
        url = client._url("/events/")
        assert "/99999/" in url


class TestPostHogClientInit:
    """Test client initialization."""

    def test_default_host(self):
        client = PostHogClient(api_key="test_key")
        assert client.host == "https://app.posthog.com"

    def test_custom_host(self):
        client = PostHogClient(api_key="test_key", host="https://eu.posthog.com/")
        assert client.host == "https://eu.posthog.com"
