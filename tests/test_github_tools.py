"""Tests for src/devrel_swarm/tools/github_tools.py using respx to mock httpx calls."""

import httpx
import respx

from devrel_swarm.tools.github_tools import (
    GITHUB_API,
    ContributorProfile,
    GitHubIssue,
    GitHubTools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_ISSUE = {
    "number": 42,
    "title": "Test issue",
    "body": "Test body",
    "user": {"login": "testuser"},
    "state": "open",
    "labels": [{"name": "bug"}],
    "created_at": "2026-03-10T00:00:00Z",
    "updated_at": "2026-03-10T00:00:00Z",
    "comments": 3,
    "reactions": {"total_count": 5},
    "html_url": "https://github.com/openclaw/openclaw/issues/42",
}

SAMPLE_ISSUE_2 = {
    "number": 99,
    "title": "Another issue",
    "body": "Another body",
    "user": {"login": "anotheruser"},
    "state": "closed",
    "labels": [{"name": "enhancement"}, {"name": "good first issue"}],
    "created_at": "2026-03-11T00:00:00Z",
    "updated_at": "2026-03-12T00:00:00Z",
    "comments": 0,
    "reactions": {"total_count": 2},
    "html_url": "https://github.com/openclaw/openclaw/issues/99",
}

SAMPLE_PR_ITEM = {
    "number": 55,
    "title": "Add feature",
    "body": "PR body",
    "user": {"login": "prauthor"},
    "state": "open",
    "labels": [],
    "created_at": "2026-03-09T00:00:00Z",
    "updated_at": "2026-03-09T00:00:00Z",
    "comments": 1,
    "reactions": {"total_count": 0},
    "html_url": "https://github.com/openclaw/openclaw/pull/55",
    "pull_request": {"url": "https://api.github.com/repos/openclaw/openclaw/pulls/55"},
}


# ---------------------------------------------------------------------------
# TestGitHubToolsInit
# ---------------------------------------------------------------------------


class TestGitHubToolsInit:
    async def test_init_with_token(self):
        gh = GitHubTools(token="ghp_testtoken123")
        try:
            assert gh._client.headers.get("authorization") == "Bearer ghp_testtoken123"
        finally:
            await gh.close()

    async def test_init_without_token(self):
        gh = GitHubTools()
        try:
            assert "authorization" not in gh._client.headers
        finally:
            await gh.close()

    async def test_init_custom_repo(self):
        gh = GitHubTools(repo="owner/custom-repo")
        try:
            assert gh.repo == "owner/custom-repo"
        finally:
            await gh.close()


# ---------------------------------------------------------------------------
# TestFetchRecentIssues
# ---------------------------------------------------------------------------


class TestFetchRecentIssues:
    async def test_fetch_issues_success(self):
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues").mock(
                return_value=httpx.Response(200, json=[SAMPLE_ISSUE, SAMPLE_ISSUE_2])
            )
            gh = GitHubTools(token="test-token")
            try:
                results = await gh.fetch_recent_issues()

                assert len(results) == 2

                first = results[0]
                assert isinstance(first, GitHubIssue)
                assert first.number == 42
                assert first.title == "Test issue"
                assert first.body == "Test body"
                assert first.author == "testuser"
                assert first.state == "open"
                assert first.labels == ["bug"]
                assert first.comments_count == 3
                assert first.reactions_total == 5
                assert first.is_pull_request is False
                assert first.url == "https://github.com/openclaw/openclaw/issues/42"

                second = results[1]
                assert second.number == 99
                assert second.labels == ["enhancement", "good first issue"]
            finally:
                await gh.close()

    async def test_fetch_issues_filters_pr(self):
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues").mock(
                return_value=httpx.Response(200, json=[SAMPLE_ISSUE, SAMPLE_PR_ITEM])
            )
            gh = GitHubTools(token="test-token")
            try:
                results = await gh.fetch_recent_issues()

                assert len(results) == 2
                issue = next(r for r in results if r.number == 42)
                pr = next(r for r in results if r.number == 55)

                assert issue.is_pull_request is False
                assert pr.is_pull_request is True
            finally:
                await gh.close()

    async def test_fetch_issues_with_labels(self):
        with respx.mock(base_url=GITHUB_API) as mock:
            route = mock.get("/repos/openclaw/openclaw/issues").mock(
                return_value=httpx.Response(200, json=[SAMPLE_ISSUE])
            )
            gh = GitHubTools(token="test-token")
            try:
                await gh.fetch_recent_issues(labels=["bug", "enhancement"])

                # Verify the request was made with labels param as comma-joined string
                request = route.calls[0].request
                assert "labels=bug%2Cenhancement" in str(
                    request.url
                ) or "labels=bug,enhancement" in str(request.url)
            finally:
                await gh.close()

    async def test_fetch_issues_empty(self):
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues").mock(
                return_value=httpx.Response(200, json=[])
            )
            gh = GitHubTools(token="test-token")
            try:
                results = await gh.fetch_recent_issues()
                assert results == []
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestGetIssue
# ---------------------------------------------------------------------------


class TestGetIssue:
    async def test_get_issue_success(self):
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues/42").mock(
                return_value=httpx.Response(200, json=SAMPLE_ISSUE)
            )
            gh = GitHubTools(token="test-token")
            try:
                issue = await gh.get_issue(42)

                assert isinstance(issue, GitHubIssue)
                assert issue.number == 42
                assert issue.title == "Test issue"
                assert issue.author == "testuser"
                assert issue.reactions_total == 5
            finally:
                await gh.close()

    async def test_get_issue_null_body(self):
        null_body_issue = {**SAMPLE_ISSUE, "body": None}
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues/42").mock(
                return_value=httpx.Response(200, json=null_body_issue)
            )
            gh = GitHubTools(token="test-token")
            try:
                issue = await gh.get_issue(42)
                assert issue.body == ""
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestGetIssueComments
# ---------------------------------------------------------------------------


class TestGetIssueComments:
    async def test_get_comments(self):
        comments_payload = [
            {
                "user": {"login": "commenter1"},
                "body": "This is a comment",
                "created_at": "2026-03-10T10:00:00Z",
                "reactions": {"total_count": 2},
            },
            {
                "user": {"login": "commenter2"},
                "body": "Another comment",
                "created_at": "2026-03-10T11:00:00Z",
                "reactions": {"total_count": 0},
            },
        ]
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/issues/42/comments").mock(
                return_value=httpx.Response(200, json=comments_payload)
            )
            gh = GitHubTools(token="test-token")
            try:
                comments = await gh.get_issue_comments(42)

                assert len(comments) == 2

                first = comments[0]
                assert first["author"] == "commenter1"
                assert first["body"] == "This is a comment"
                assert first["created_at"] == "2026-03-10T10:00:00Z"
                assert first["reactions"] == 2

                second = comments[1]
                assert second["author"] == "commenter2"
                assert second["reactions"] == 0
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestContributorProfile
# ---------------------------------------------------------------------------


class TestContributorProfile:
    async def test_get_profile(self):
        issues_response = {"total_count": 12, "items": []}
        prs_response = {"total_count": 7, "items": []}
        comments_response = {"total_count": 34, "items": []}

        with respx.mock(base_url=GITHUB_API) as mock:
            # All 3 calls go to /search/issues — match them in order
            mock.get("/search/issues").mock(
                side_effect=[
                    httpx.Response(200, json=issues_response),
                    httpx.Response(200, json=prs_response),
                    httpx.Response(200, json=comments_response),
                ]
            )
            gh = GitHubTools(token="test-token")
            try:
                profile = await gh.get_contributor_profile("someuser")

                assert isinstance(profile, ContributorProfile)
                assert profile.username == "someuser"
                assert profile.total_issues == 12
                assert profile.total_prs == 7
                assert profile.total_comments == 34
                assert profile.is_maintainer is False
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestSearchSimilarIssues
# ---------------------------------------------------------------------------


class TestSearchSimilarIssues:
    async def test_search_success(self):
        search_response = {
            "total_count": 2,
            "items": [SAMPLE_ISSUE, SAMPLE_ISSUE_2],
        }
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/search/issues").mock(return_value=httpx.Response(200, json=search_response))
            gh = GitHubTools(token="test-token")
            try:
                results = await gh.search_similar_issues("feature flag crash")

                assert len(results) == 2
                assert isinstance(results[0], GitHubIssue)
                assert results[0].number == 42
                assert results[1].number == 99
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestLabels
# ---------------------------------------------------------------------------


class TestLabels:
    async def test_list_labels(self):
        labels_payload = [
            {"name": "bug", "color": "d73a4a", "description": "Something isn't working"},
            {"name": "enhancement", "color": "a2eeef", "description": "New feature"},
            {"name": "good first issue", "color": "7057ff", "description": None},
        ]
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw/labels").mock(
                return_value=httpx.Response(200, json=labels_payload)
            )
            gh = GitHubTools(token="test-token")
            try:
                labels = await gh.list_labels()

                assert len(labels) == 3
                assert labels[0] == {
                    "name": "bug",
                    "color": "d73a4a",
                    "description": "Something isn't working",
                }
                assert labels[1]["name"] == "enhancement"
                # description key present but None → coerced to "" by `or ""`
                assert labels[2]["description"] == ""
            finally:
                await gh.close()

    async def test_add_labels(self):
        response_payload = [
            {"name": "bug", "color": "d73a4a"},
            {"name": "priority", "color": "e11d48"},
        ]
        with respx.mock(base_url=GITHUB_API) as mock:
            route = mock.post("/repos/openclaw/openclaw/issues/42/labels").mock(
                return_value=httpx.Response(200, json=response_payload)
            )
            gh = GitHubTools(token="test-token")
            try:
                result = await gh.add_labels(42, ["bug", "priority"])

                # Verify correct payload was sent
                request = route.calls[0].request
                import json

                body = json.loads(request.content)
                assert body == {"labels": ["bug", "priority"]}

                assert result == response_payload
            finally:
                await gh.close()


# ---------------------------------------------------------------------------
# TestRepoStats
# ---------------------------------------------------------------------------


class TestRepoStats:
    async def test_get_stats(self):
        repo_payload = {
            "stargazers_count": 22500,
            "forks_count": 4200,
            "open_issues_count": 380,
            "subscribers_count": 310,
            "language": "TypeScript",
            "updated_at": "2026-03-12T18:00:00Z",
            "name": "posthog",
            "full_name": "openclaw/openclaw",
        }
        with respx.mock(base_url=GITHUB_API) as mock:
            mock.get("/repos/openclaw/openclaw").mock(
                return_value=httpx.Response(200, json=repo_payload)
            )
            gh = GitHubTools(token="test-token")
            try:
                stats = await gh.get_repo_stats()

                assert stats["stars"] == 22500
                assert stats["forks"] == 4200
                assert stats["open_issues"] == 380
                assert stats["watchers"] == 310
                assert stats["language"] == "TypeScript"
                assert stats["updated_at"] == "2026-03-12T18:00:00Z"
            finally:
                await gh.close()
