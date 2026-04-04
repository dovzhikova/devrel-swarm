"""
PostHog API v2 async client (legacy — retained for interface compatibility).

Originally a typed, retryable wrapper around PostHog's REST API.
OpenClaw does not have an equivalent external API, so this module is kept
as a structural dependency for agent imports but is not used for live API calls.
The PostHogClient class and its DTOs remain functional for testing and
reference purposes.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_HOST = "https://app.posthog.com"
API_TIMEOUT = 30.0
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class InsightQuery:
    """Parameters for a PostHog insight query."""

    insight: str = "TRENDS"  # TRENDS, FUNNELS, RETENTION, PATHS, LIFECYCLE
    events: list[dict[str, Any]] = field(default_factory=list)
    properties: list[dict[str, Any]] = field(default_factory=list)
    date_from: str = "-7d"
    date_to: Optional[str] = None
    interval: str = "day"
    breakdown: Optional[str] = None
    breakdown_type: Optional[str] = None
    filter_test_accounts: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "insight": self.insight,
            "events": self.events,
            "properties": self.properties,
            "date_from": self.date_from,
            "interval": self.interval,
            "filter_test_accounts": self.filter_test_accounts,
        }
        if self.date_to:
            d["date_to"] = self.date_to
        if self.breakdown:
            d["breakdown"] = self.breakdown
            d["breakdown_type"] = self.breakdown_type or "event"
        return d


@dataclass
class FeatureFlag:
    """PostHog feature flag representation."""

    key: str
    name: str = ""
    active: bool = True
    rollout_percentage: Optional[int] = None
    filters: dict[str, Any] = field(default_factory=dict)
    ensure_experience_continuity: bool = False


@dataclass
class Experiment:
    """PostHog experiment representation."""

    name: str
    feature_flag_key: str
    description: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PostHogClient:
    """
    Typed async client for the PostHog REST API v2.

    Usage::

        client = PostHogClient(api_key="phx_...", project_id="12345")
        trends = await client.query_insights(
            InsightQuery(events=[{"id": "$pageview"}])
        )
    """

    def __init__(
        self,
        api_key: str,
        project_id: str = "",
        host: str = DEFAULT_HOST,
    ):
        self.api_key = api_key
        self.project_id = project_id
        self.host = host.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.host,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=API_TIMEOUT,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # -- helpers ----------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a project-scoped API URL."""
        if self.project_id:
            return f"/api/projects/{self.project_id}{path}"
        return f"/api{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with retry logic."""
        url = self._url(path)
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = await self._client.request(method, url, json=json, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = exc
                logger.warning(f"PostHog API {method} {url} failed (attempt {attempt}): {exc}")
                if attempt <= MAX_RETRIES:
                    import asyncio

                    await asyncio.sleep(1.0 * attempt)

        raise last_error  # type: ignore[misc]

    # -- Event Capture ----------------------------------------------------

    async def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Capture a single event."""
        return await self._request(
            "POST",
            "/capture/",
            json={
                "api_key": self.api_key,
                "distinct_id": distinct_id,
                "event": event,
                "properties": properties or {},
            },
        )

    async def capture_batch(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Capture a batch of events."""
        return await self._request(
            "POST",
            "/capture/",
            json={"api_key": self.api_key, "batch": events},
        )

    # -- Insights / Queries -----------------------------------------------

    async def query_insights(self, query: InsightQuery) -> dict[str, Any]:
        """Run an insight query (trends, funnels, retention, etc.)."""
        return await self._request("POST", "/insights/", json=query.to_dict())

    async def get_insight(self, insight_id: int) -> dict[str, Any]:
        """Fetch a saved insight by ID."""
        return await self._request("GET", f"/insights/{insight_id}/")

    async def list_insights(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List saved insights with pagination."""
        return await self._request(
            "GET",
            "/insights/",
            params={"limit": limit, "offset": offset},
        )

    # -- Feature Flags ----------------------------------------------------

    async def create_feature_flag(self, flag: FeatureFlag) -> dict[str, Any]:
        """Create a new feature flag."""
        payload: dict[str, Any] = {
            "key": flag.key,
            "name": flag.name,
            "active": flag.active,
            "filters": flag.filters,
            "ensure_experience_continuity": flag.ensure_experience_continuity,
        }
        if flag.rollout_percentage is not None:
            payload["rollout_percentage"] = flag.rollout_percentage
        return await self._request("POST", "/feature_flags/", json=payload)

    async def get_feature_flag(self, flag_id: int) -> dict[str, Any]:
        """Fetch a feature flag by ID."""
        return await self._request("GET", f"/feature_flags/{flag_id}/")

    async def list_feature_flags(self, limit: int = 100) -> dict[str, Any]:
        """List all feature flags."""
        return await self._request("GET", "/feature_flags/", params={"limit": limit})

    async def update_feature_flag(self, flag_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        """Patch a feature flag."""
        return await self._request("PATCH", f"/feature_flags/{flag_id}/", json=updates)

    async def delete_feature_flag(self, flag_id: int) -> dict[str, Any]:
        """Delete a feature flag."""
        return await self._request("DELETE", f"/feature_flags/{flag_id}/")

    # -- Experiments ------------------------------------------------------

    async def create_experiment(self, experiment: Experiment) -> dict[str, Any]:
        """Create a new experiment."""
        return await self._request(
            "POST",
            "/experiments/",
            json={
                "name": experiment.name,
                "feature_flag_key": experiment.feature_flag_key,
                "description": experiment.description,
                "start_date": experiment.start_date,
                "end_date": experiment.end_date,
                "parameters": experiment.parameters,
            },
        )

    async def get_experiment(self, experiment_id: int) -> dict[str, Any]:
        """Fetch an experiment by ID."""
        return await self._request("GET", f"/experiments/{experiment_id}/")

    async def list_experiments(self, limit: int = 100) -> dict[str, Any]:
        """List all experiments."""
        return await self._request("GET", "/experiments/", params={"limit": limit})

    async def get_experiment_results(self, experiment_id: int) -> dict[str, Any]:
        """Fetch experiment results with statistical analysis."""
        return await self._request("GET", f"/experiments/{experiment_id}/results/")

    # -- Cohorts ----------------------------------------------------------

    async def create_cohort(
        self,
        name: str,
        groups: list[dict[str, Any]],
        is_static: bool = False,
    ) -> dict[str, Any]:
        """Create a new cohort."""
        return await self._request(
            "POST",
            "/cohorts/",
            json={
                "name": name,
                "groups": groups,
                "is_static": is_static,
            },
        )

    async def get_cohort(self, cohort_id: int) -> dict[str, Any]:
        """Fetch a cohort by ID."""
        return await self._request("GET", f"/cohorts/{cohort_id}/")

    async def list_cohorts(self, limit: int = 100) -> dict[str, Any]:
        """List all cohorts."""
        return await self._request("GET", "/cohorts/", params={"limit": limit})

    # -- Annotations ------------------------------------------------------

    async def create_annotation(
        self,
        content: str,
        date_marker: str,
        scope: str = "organization",
    ) -> dict[str, Any]:
        """Create a date annotation (e.g., deploy marker)."""
        return await self._request(
            "POST",
            "/annotations/",
            json={
                "content": content,
                "date_marker": date_marker,
                "scope": scope,
            },
        )

    async def list_annotations(self, limit: int = 100) -> dict[str, Any]:
        """List all annotations."""
        return await self._request("GET", "/annotations/", params={"limit": limit})

    # -- Persons ----------------------------------------------------------

    async def get_person(self, distinct_id: str) -> dict[str, Any]:
        """Look up a person by distinct_id."""
        result = await self._request(
            "GET",
            "/persons/",
            params={"distinct_id": distinct_id},
        )
        persons = result.get("results", [])
        if not persons:
            raise ValueError(f"No person found for distinct_id={distinct_id}")
        return persons[0]

    async def list_persons(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List persons with pagination."""
        return await self._request(
            "GET",
            "/persons/",
            params={"limit": limit, "offset": offset},
        )

    # -- Actions ----------------------------------------------------------

    async def list_actions(self, limit: int = 100) -> dict[str, Any]:
        """List defined actions."""
        return await self._request("GET", "/actions/", params={"limit": limit})

    # -- Session Recordings -----------------------------------------------

    async def list_session_recordings(
        self,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
    ) -> dict[str, Any]:
        """List session recordings."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if date_from:
            params["date_from"] = date_from
        return await self._request("GET", "/session_recordings/", params=params)
