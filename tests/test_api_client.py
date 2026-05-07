"""Tests for PostHog API client module."""

import httpx
import pytest
import respx

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


# ---------------------------------------------------------------------------
# Fixtures for live-client tests (respx intercepts the real httpx calls)
# ---------------------------------------------------------------------------


@pytest.fixture
def posthog_client():
    """Real PostHogClient wired against project 1 (respx intercepts httpx)."""
    return PostHogClient(api_key="test_key", project_id="1")


# ---------------------------------------------------------------------------
# event_volumes + funnel_query
# ---------------------------------------------------------------------------


@respx.mock
async def test_event_volumes_returns_top_events(posthog_client):
    respx.post("https://app.posthog.com/api/projects/1/query/").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    ["$pageview", 12500],
                    ["signup_started", 3200],
                    ["signup_completed", 1850],
                ],
            },
        )
    )
    out = await posthog_client.event_volumes(days=7, limit=10)
    assert out[0] == ("$pageview", 12500)
    assert len(out) == 3


@respx.mock
async def test_funnel_query_returns_step_conversion_rates(posthog_client):
    respx.post("https://app.posthog.com/api/projects/1/query/").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "$pageview",
                        "count": 1000,
                        "average_conversion_time": 0,
                    },
                    {
                        "name": "signup_started",
                        "count": 300,
                        "average_conversion_time": 120,
                    },
                    {
                        "name": "signup_completed",
                        "count": 120,
                        "average_conversion_time": 600,
                    },
                ]
            },
        )
    )
    steps = await posthog_client.funnel_query(
        events=["$pageview", "signup_started", "signup_completed"],
        days=7,
    )
    assert len(steps) == 3
    assert steps[0]["name"] == "$pageview"
    assert steps[0]["count"] == 1000
    assert steps[2]["count"] == 120
