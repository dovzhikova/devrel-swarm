# Growth Pipeline Wave 3 — Selene (SEO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Selene, the SEO auditor — sitemap-driven crawl with on-page heuristic checks, LLM gap analysis using Rex's competitor pages, and Google Search Console keyword performance via OAuth user-flow.

**Architecture:** New `core/selene.py` agent class. Net-new `tools/gsc_client.py` (full OAuth installed-app flow + searchanalytics.query wrapper) and `tools/seo_crawler.py` (async sitemap walker + BeautifulSoup parser, honors robots.txt). New `core/oauth_constants.py` carries the shared "devrel-origin" GCP project's client_id/secret. Crawl HTML cached at `.devrel/seo/crawls/`. Persistence + lifecycle via `core/growth/recommendations.py` (Wave 0).

**Tech Stack:** Python 3.12 async, httpx, BeautifulSoup4, google-api-python-client + google-auth-oauthlib, Anthropic Claude Sonnet for gap analysis, pytest + respx + httpx mocks.

**Spec:** `docs/superpowers/specs/2026-05-05-growth-pipeline-design.md`
**Depends on:** Wave 0 (schema v5, growth module). Independent of Wave 1 + Wave 2.
**External dependency:** GCP project + OAuth client created in Wave 0 Task 10. The verification submission should be in flight by now.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_origin/core/oauth_constants.py` | Create | Holds the shared `GSC_OAUTH_CLIENT_ID`/`SECRET` — env-overridable for self-hosters |
| `src/devrel_origin/tools/gsc_client.py` | Create | OAuth installed-app flow, token storage, `searchanalytics.query` wrapper |
| `src/devrel_origin/tools/seo_crawler.py` | Create | Async sitemap walker + page parser + robots.txt enforcement |
| `src/devrel_origin/core/selene.py` | Create | Selene agent — orchestrator + heuristic checks + gap analysis + decay detection |
| `src/devrel_origin/core/__init__.py` | Modify | Export `Selene` |
| `src/devrel_origin/cli/seo.py` | Create | Typer `seo_app` with `connect-gsc`/`crawl`/`report`/`history`/`diff`/`calibration` |
| `src/devrel_origin/cli/__init__.py` | Modify | Register `seo_app` |
| `tests/test_gsc_client.py` | Create | OAuth flow + searchanalytics tests |
| `tests/test_seo_crawler.py` | Create | Sitemap + page parser + robots tests |
| `tests/test_selene.py` | Create | Selene end-to-end (heuristics + gap + decay + persist + brief) |
| `tests/cli/test_seo_command.py` | Create | CLI verb smoke tests |

---

## Task 1: `core/oauth_constants.py`

**Files:**
- Create: `src/devrel_origin/core/oauth_constants.py`
- Test: `tests/core/test_oauth_constants.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_oauth_constants.py`:

```python
"""Test that oauth_constants reads env-vars when present."""

import importlib

from devrel_origin.core import oauth_constants


def test_default_client_id_is_set():
    assert oauth_constants.GSC_OAUTH_CLIENT_ID  # non-empty string
    assert ".apps.googleusercontent.com" in oauth_constants.GSC_OAUTH_CLIENT_ID


def test_env_var_override(monkeypatch):
    monkeypatch.setenv("GSC_OAUTH_CLIENT_ID", "override.apps.googleusercontent.com")
    monkeypatch.setenv("GSC_OAUTH_CLIENT_SECRET", "override-secret")
    importlib.reload(oauth_constants)
    assert oauth_constants.GSC_OAUTH_CLIENT_ID == "override.apps.googleusercontent.com"
    assert oauth_constants.GSC_OAUTH_CLIENT_SECRET == "override-secret"
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/core/test_oauth_constants.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Create the module**

Create `src/devrel_origin/core/oauth_constants.py`:

```python
"""OAuth client constants for the shared `devrel-origin` GCP project.

These identify the installed-app OAuth client. Embedding `client_secret`
in the package is intentional and safe per Google's installed-app guidance:
the secret authenticates the app to Google, not the user (the user authenticates
against their own Google account during the consent flow). See
https://developers.google.com/identity/protocols/oauth2/native-app.

Self-hosters can override either value via env var.
"""

from __future__ import annotations

import os

# Default values point at the shared "devrel-origin" GCP project owned by Daria.
# Self-hosters override via env vars to point at their own project.
GSC_OAUTH_CLIENT_ID: str = os.getenv(
    "GSC_OAUTH_CLIENT_ID",
    # Replace with the actual client_id created in Wave 0 Task 10.
    "REPLACE_AFTER_WAVE0_TASK10.apps.googleusercontent.com",
)
GSC_OAUTH_CLIENT_SECRET: str = os.getenv(
    "GSC_OAUTH_CLIENT_SECRET",
    "REPLACE_AFTER_WAVE0_TASK10",
)

# Read-only Search Console scope.
GSC_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/core/test_oauth_constants.py -v --no-cov
```

Expected: PASS for env-override test; the default-id test will FAIL until the actual values from Wave 0 Task 10 are pasted in. Mark that test as expected to fail until then:

```python
import pytest

@pytest.mark.skip(reason="Defaults pasted from GCP after Wave 0 Task 10 completes")
def test_default_client_id_is_set():
    ...
```

(After Daria runs through the GCP setup walkthrough and pastes the real client_id/secret, unmark this test.)

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/oauth_constants.py tests/core/test_oauth_constants.py
git commit -m "feat(oauth): GSC OAuth constants with env-var override path"
```

---

## Task 2: GSC OAuth flow scaffolding

**Files:**
- Create: `src/devrel_origin/tools/gsc_client.py`
- Test: `tests/test_gsc_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gsc_client.py`:

```python
"""Tests for the GSC client OAuth flow + token storage.

The actual browser OAuth round-trip is monkeypatched; we verify the
storage path and the API wrapper independently.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devrel_origin.tools.gsc_client import GSCClient, OAuthError


class TestTokenStorage:
    def test_save_and_load_credentials(self, tmp_path):
        creds_path = tmp_path / "gsc.json"
        client = GSCClient(creds_path=creds_path)
        client._save_credentials({
            "token": "ya29.test", "refresh_token": "1//refresh",
            "client_id": "id.apps.googleusercontent.com",
            "client_secret": "secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/webmasters.readonly"],
        })
        assert creds_path.is_file()
        # File mode is 0600 (read/write owner only)
        mode = creds_path.stat().st_mode & 0o777
        assert mode == 0o600

        loaded = GSCClient(creds_path=creds_path)._load_credentials()
        assert loaded is not None
        assert loaded["refresh_token"] == "1//refresh"

    def test_load_missing_returns_none(self, tmp_path):
        creds_path = tmp_path / "missing.json"
        out = GSCClient(creds_path=creds_path)._load_credentials()
        assert out is None


class TestOAuthFlowEntrypoint:
    def test_connect_raises_when_constants_unset(self, tmp_path, monkeypatch):
        """If oauth_constants is still placeholder, connect() raises clearly."""
        monkeypatch.setattr(
            "devrel_origin.core.oauth_constants.GSC_OAUTH_CLIENT_ID",
            "REPLACE_AFTER_WAVE0_TASK10.apps.googleusercontent.com",
        )
        client = GSCClient(creds_path=tmp_path / "gsc.json")
        with pytest.raises(OAuthError, match="OAuth client not configured"):
            client.connect()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_gsc_client.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the OAuth flow + storage**

Create `src/devrel_origin/tools/gsc_client.py`:

```python
"""Google Search Console client.

OAuth: installed-app flow against the shared `devrel-origin` GCP project.
Tokens stored at `.devrel/credentials/gsc.json` with mode 0600.

API: `searchanalytics.query` wrapper for keyword performance metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class OAuthError(RuntimeError):
    """Raised when OAuth flow can't proceed (missing constants, network failure, etc.)."""


class GSCClient:
    """Read-only Google Search Console client with OAuth installed-app flow."""

    def __init__(self, *, creds_path: Path):
        self.creds_path = creds_path

    # ──────────────────────────────────────────────────────────────────
    # OAuth flow + token storage
    # ──────────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Run the installed-app OAuth flow.

        Opens the user's default browser to Google's consent screen,
        listens on localhost:8765 for the callback, exchanges the code
        for a refresh token, and persists credentials to `creds_path`.
        """
        from devrel_origin.core.oauth_constants import (
            GSC_OAUTH_CLIENT_ID,
            GSC_OAUTH_CLIENT_SECRET,
            GSC_SCOPES,
        )

        if (
            "REPLACE_AFTER_WAVE0_TASK10" in GSC_OAUTH_CLIENT_ID
            or "REPLACE_AFTER_WAVE0_TASK10" in GSC_OAUTH_CLIENT_SECRET
        ):
            raise OAuthError(
                "OAuth client not configured. The maintainer must paste the "
                "real client_id/secret into core/oauth_constants.py "
                "(see docs/setup-google-oauth.md)."
            )

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            raise ImportError(
                "GSCClient requires `pip install 'devrel-origin[seo]'`"
            ) from e

        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": GSC_OAUTH_CLIENT_ID,
                    "client_secret": GSC_OAUTH_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8765/"],
                }
            },
            scopes=GSC_SCOPES,
        )
        # `run_local_server` opens browser + listens on the given port
        creds = flow.run_local_server(port=8765, prompt="consent", access_type="offline")

        self._save_credentials({
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "token_uri": creds.token_uri,
            "scopes": creds.scopes,
        })

    def _save_credentials(self, data: dict) -> None:
        self.creds_path.parent.mkdir(parents=True, exist_ok=True)
        self.creds_path.write_text(json.dumps(data, indent=2))
        # Restrict to owner-only (defensive for $HOME-readable paths)
        os.chmod(self.creds_path, stat.S_IRUSR | stat.S_IWUSR)

    def _load_credentials(self) -> Optional[dict]:
        if not self.creds_path.is_file():
            return None
        return json.loads(self.creds_path.read_text())

    def is_connected(self) -> bool:
        return self._load_credentials() is not None

    # ──────────────────────────────────────────────────────────────────
    # API wrappers — added in next task
    # ──────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gsc_client.py -v --no-cov
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/gsc_client.py tests/test_gsc_client.py
git commit -m "feat(gsc): OAuth installed-app flow scaffolding + token storage"
```

---

## Task 3: GSC `searchanalytics.query` wrapper

**Files:**
- Modify: `src/devrel_origin/tools/gsc_client.py`
- Modify: `tests/test_gsc_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gsc_client.py`:

```python
class TestSearchAnalyticsQuery:
    @pytest.mark.asyncio
    async def test_query_returns_keyword_metrics(self, tmp_path, monkeypatch):
        # Stub the credentials + the googleapiclient build
        creds_path = tmp_path / "gsc.json"
        creds_path.write_text(json.dumps({
            "token": "ya29.test", "refresh_token": "1//refresh",
            "client_id": "id", "client_secret": "secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/webmasters.readonly"],
        }))

        # Mock the googleapiclient discovery + chained call
        fake_response = {
            "rows": [
                {"keys": ["openclaw observability", "https://openclaw.ai/"],
                 "clicks": 100, "impressions": 5000, "ctr": 0.02, "position": 8.5},
                {"keys": ["kubernetes monitoring", "https://openclaw.ai/docs"],
                 "clicks": 30, "impressions": 2000, "ctr": 0.015, "position": 12.0},
            ]
        }
        mock_service = MagicMock()
        mock_service.searchanalytics().query().execute.return_value = fake_response

        with patch("googleapiclient.discovery.build", return_value=mock_service), \
             patch("google.oauth2.credentials.Credentials.from_authorized_user_info"):
            client = GSCClient(creds_path=creds_path)
            rows = await client.search_analytics_query(
                site_url="https://openclaw.ai/",
                start=date(2026, 4, 1), end=date(2026, 4, 8),
                dimensions=["query", "page"],
            )

        assert len(rows) == 2
        assert rows[0]["keyword"] == "openclaw observability"
        assert rows[0]["page"] == "https://openclaw.ai/"
        assert rows[0]["clicks"] == 100
        assert rows[0]["impressions"] == 5000
        assert rows[0]["ctr"] == pytest.approx(0.02)
        assert rows[0]["position"] == pytest.approx(8.5)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_gsc_client.py::TestSearchAnalyticsQuery -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the search_analytics_query method**

Append to `GSCClient`:

```python
    async def search_analytics_query(
        self,
        *,
        site_url: str,
        start: date,
        end: date,
        dimensions: list[str] | None = None,
        row_limit: int = 25000,
    ) -> list[dict]:
        """Fetch keyword/page performance from Search Console for the date range.

        Returns: [{keyword, page, clicks, impressions, ctr, position}, ...]
        for `dimensions=['query','page']` (the default).
        """
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as e:
            raise ImportError(
                "GSCClient requires `pip install 'devrel-origin[seo]'`"
            ) from e

        data = self._load_credentials()
        if data is None:
            raise OAuthError(
                "Not connected. Run `devrel seo connect-gsc` first."
            )
        creds = Credentials.from_authorized_user_info(data)

        dims = dimensions or ["query", "page"]

        # `googleapiclient` is sync; run in a thread.
        def _run() -> dict:
            service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
            return service.searchanalytics().query(
                siteUrl=site_url,
                body={
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "dimensions": dims,
                    "rowLimit": row_limit,
                },
            ).execute()

        result = await asyncio.to_thread(_run)
        rows: list[dict] = []
        for row in result.get("rows", []) or []:
            keys = row.get("keys", [])
            entry = {
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": float(row.get("ctr", 0.0)),
                "position": float(row.get("position", 0.0)),
            }
            for i, dim in enumerate(dims):
                if i < len(keys):
                    if dim == "query":
                        entry["keyword"] = keys[i]
                    elif dim == "page":
                        entry["page"] = keys[i]
                    else:
                        entry[dim] = keys[i]
            rows.append(entry)
        return rows
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_gsc_client.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/gsc_client.py tests/test_gsc_client.py
git commit -m "feat(gsc): searchanalytics.query wrapper with date+dimension support"
```

---

## Task 4: SEO crawler — sitemap walker + robots.txt

**Files:**
- Create: `src/devrel_origin/tools/seo_crawler.py`
- Test: `tests/test_seo_crawler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_seo_crawler.py`:

```python
"""Tests for the async sitemap walker and robots.txt parser."""

import pytest
import respx
from httpx import Response

from devrel_origin.tools.seo_crawler import SEOCrawler, SitemapEntry


@pytest.fixture
def crawler():
    return SEOCrawler(crawl_delay_ms=0, max_pages=10)  # delay=0 keeps tests fast


@respx.mock
@pytest.mark.asyncio
async def test_fetch_sitemap_parses_urls(crawler):
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://openclaw.ai/</loc></url>
  <url><loc>https://openclaw.ai/docs</loc></url>
  <url><loc>https://openclaw.ai/pricing</loc></url>
</urlset>"""
    respx.get("https://openclaw.ai/sitemap.xml").mock(
        return_value=Response(200, text=sitemap_xml, headers={"content-type": "application/xml"})
    )
    entries = await crawler.fetch_sitemap("https://openclaw.ai/sitemap.xml")
    assert len(entries) == 3
    assert entries[0].url == "https://openclaw.ai/"


@respx.mock
@pytest.mark.asyncio
async def test_robots_disallow_is_respected(crawler):
    respx.get("https://openclaw.ai/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nDisallow: /admin\n")
    )
    allowed1 = await crawler.is_allowed("https://openclaw.ai/", user_agent="*")
    allowed2 = await crawler.is_allowed("https://openclaw.ai/admin/secret", user_agent="*")
    assert allowed1 is True
    assert allowed2 is False


@respx.mock
@pytest.mark.asyncio
async def test_max_pages_caps_walk(crawler):
    """If sitemap has 50 URLs and max_pages=10, only 10 are returned."""
    sitemap_urls = "".join(
        f"<url><loc>https://openclaw.ai/p{i}</loc></url>" for i in range(50)
    )
    sitemap_xml = (
        f'<?xml version="1.0"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{sitemap_urls}</urlset>'
    )
    respx.get("https://openclaw.ai/sitemap.xml").mock(
        return_value=Response(200, text=sitemap_xml,
                              headers={"content-type": "application/xml"})
    )
    entries = await crawler.fetch_sitemap("https://openclaw.ai/sitemap.xml")
    assert len(entries) == 10  # capped by max_pages
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_seo_crawler.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the crawler scaffold**

Create `src/devrel_origin/tools/seo_crawler.py`:

```python
"""Async sitemap walker + page parser + robots.txt enforcement.

Used by Selene to fetch and parse pages from the user's product website.
Honors `crawl_delay_ms` between requests (default 1000ms — polite to the
user's site) and caps total pages at `max_pages` (default 200).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "devrel-origin/0.3 (Selene SEO crawler; +https://gtm-labs.co/devrel-origin)"


@dataclass
class SitemapEntry:
    url: str
    lastmod: Optional[str] = None
    priority: Optional[float] = None


class SEOCrawler:
    """Polite async sitemap walker.

    Reuses one `httpx.AsyncClient` across the crawl. Caches HTML to disk
    for replay/debug when `cache_dir` is set.
    """

    def __init__(
        self,
        *,
        crawl_delay_ms: int = 1000,
        max_pages: int = 200,
        cache_dir: Optional[Path] = None,
    ):
        self.crawl_delay_ms = crawl_delay_ms
        self.max_pages = max_pages
        self.cache_dir = cache_dir
        self._robots_cache: dict[str, RobotFileParser] = {}
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                timeout=15.0,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_sitemap(self, sitemap_url: str) -> list[SitemapEntry]:
        client = await self._ensure_client()
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

        # Strip XML namespace for simpler parsing
        text = resp.text
        # Quick namespace strip: replace `xmlns="..."` so ET.find works without prefix
        import re
        text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)

        root = ET.fromstring(text)
        entries: list[SitemapEntry] = []
        for url_el in root.findall("url"):
            loc = url_el.find("loc")
            if loc is None or not loc.text:
                continue
            entries.append(SitemapEntry(
                url=loc.text.strip(),
                lastmod=(url_el.find("lastmod").text if url_el.find("lastmod") is not None else None),
                priority=(
                    float(url_el.find("priority").text)
                    if url_el.find("priority") is not None and url_el.find("priority").text
                    else None
                ),
            ))
            if len(entries) >= self.max_pages:
                break

        return entries

    async def is_allowed(self, url: str, *, user_agent: str = USER_AGENT) -> bool:
        """Check robots.txt for the URL's host. Caches per-host."""
        parsed = urlparse(url)
        host_root = f"{parsed.scheme}://{parsed.netloc}"
        if host_root not in self._robots_cache:
            client = await self._ensure_client()
            try:
                robots_resp = await client.get(urljoin(host_root, "/robots.txt"))
            except httpx.HTTPError:
                # If robots fetch fails, default to allow (most common interpretation)
                rp = RobotFileParser()
                rp.parse([])
                self._robots_cache[host_root] = rp
                return True

            rp = RobotFileParser()
            if robots_resp.status_code == 200:
                rp.parse(robots_resp.text.splitlines())
            else:
                rp.parse([])
            self._robots_cache[host_root] = rp

        return self._robots_cache[host_root].can_fetch(user_agent, url)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_seo_crawler.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/seo_crawler.py tests/test_seo_crawler.py
git commit -m "feat(seo): async sitemap walker with robots.txt + max_pages cap"
```

---

## Task 5: Page parser with BeautifulSoup → `PageProfile`

**Files:**
- Modify: `src/devrel_origin/tools/seo_crawler.py`
- Modify: `tests/test_seo_crawler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_seo_crawler.py`:

```python
class TestPageParse:
    @pytest.mark.asyncio
    async def test_parse_page_extracts_seo_signals(self, crawler):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>OpenClaw — Open-source Kubernetes observability</title>
            <meta name="description" content="OpenClaw auto-instruments your apps for unified telemetry.">
            <script type="application/ld+json">{"@type":"SoftwareApplication"}</script>
        </head>
        <body>
            <h1>OpenClaw</h1>
            <h2>Quickstart</h2>
            <h2>Architecture</h2>
            <p>Some content here, ~200 words of body copy describing the product
            in detail, with multiple sentences and paragraphs of meaningful text
            content that contributes to word count metrics for SEO heuristics.</p>
            <a href="/docs">Docs</a>
            <a href="/pricing">Pricing</a>
            <a href="https://external.com">External link</a>
        </body>
        </html>
        """
        profile = await crawler.parse_page(html, page_url="https://openclaw.ai/")
        assert profile.url == "https://openclaw.ai/"
        assert profile.title == "OpenClaw — Open-source Kubernetes observability"
        assert profile.title_len == len(profile.title)
        assert profile.meta_description and "auto-instruments" in profile.meta_description
        assert profile.meta_len == len(profile.meta_description)
        assert profile.h1_count == 1
        assert profile.h_counts == {"h1": 1, "h2": 2, "h3": 0, "h4": 0, "h5": 0, "h6": 0}
        assert profile.has_schema is True
        assert profile.internal_links_count == 2  # /docs and /pricing (same host)
        assert profile.external_links_count == 1
        assert profile.word_count > 30
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_seo_crawler.py::TestPageParse -v --no-cov
```

Expected: AttributeError on `parse_page`.

- [ ] **Step 3: Implement `parse_page` + `PageProfile`**

Append to `seo_crawler.py`:

```python
@dataclass
class PageProfile:
    """SEO-relevant signals extracted from a single page."""

    url: str
    title: str
    title_len: int
    meta_description: Optional[str]
    meta_len: int
    h1_count: int
    h_counts: dict[str, int]      # {'h1': N, 'h2': N, ...}
    has_schema: bool              # JSON-LD or microdata present
    internal_links_count: int     # same-host hrefs
    external_links_count: int     # different-host hrefs
    word_count: int
    crawled_at: str               # ISO timestamp


async def _parse_page_impl(html: str, *, page_url: str) -> PageProfile:
    """Pure parsing helper — no I/O. Lives at module scope so tests can mock."""
    from datetime import datetime, timezone
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise ImportError(
            "SEO crawler requires `pip install 'devrel-origin[seo]'`"
        ) from e

    soup = BeautifulSoup(html, "html.parser")
    page_host = urlparse(page_url).netloc.lower()

    # Title
    title_tag = soup.find("title")
    title = (title_tag.get_text(strip=True) if title_tag else "")[:1000]

    # Meta description
    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = (
        meta_tag.get("content", "").strip()
        if meta_tag is not None else ""
    )

    # Heading counts
    h_counts = {f"h{i}": len(soup.find_all(f"h{i}")) for i in range(1, 7)}

    # Schema.org presence: JSON-LD or microdata `itemscope`
    has_schema = (
        soup.find("script", attrs={"type": "application/ld+json"}) is not None
        or soup.find(attrs={"itemscope": True}) is not None
    )

    # Link split: internal vs external
    internal = 0
    external = 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        # Resolve relative
        absolute = urljoin(page_url, href)
        link_host = urlparse(absolute).netloc.lower()
        if link_host == page_host or link_host == "":
            internal += 1
        else:
            external += 1

    # Word count: visible text only
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    word_count = len(text.split())

    return PageProfile(
        url=page_url,
        title=title,
        title_len=len(title),
        meta_description=meta_desc or None,
        meta_len=len(meta_desc),
        h1_count=h_counts["h1"],
        h_counts=h_counts,
        has_schema=has_schema,
        internal_links_count=internal,
        external_links_count=external,
        word_count=word_count,
        crawled_at=datetime.now(timezone.utc).isoformat(),
    )


# Add the method to SEOCrawler:
class SEOCrawler:
    # ... existing __init__, fetch_sitemap, is_allowed ...

    async def parse_page(self, html: str, *, page_url: str) -> PageProfile:
        return await _parse_page_impl(html, page_url=page_url)

    async def fetch_and_parse(self, page_url: str) -> Optional[PageProfile]:
        """Fetch HTML, sleep crawl_delay_ms, parse, optionally cache."""
        client = await self._ensure_client()
        if not await self.is_allowed(page_url):
            logger.info(f"SEO crawler: robots.txt blocks {page_url}")
            return None
        try:
            resp = await client.get(page_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"SEO crawler: fetch failed for {page_url}: {e}")
            return None

        if self.cache_dir is not None:
            slug = (
                urlparse(page_url).path.replace("/", "_") or "_root"
            )[:80]
            cache_file = self.cache_dir / f"{slug}.html"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(resp.text)

        if self.crawl_delay_ms > 0:
            await asyncio.sleep(self.crawl_delay_ms / 1000.0)

        return await self.parse_page(resp.text, page_url=page_url)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_seo_crawler.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/seo_crawler.py tests/test_seo_crawler.py
git commit -m "feat(seo): page parser → PageProfile with all SEO signals"
```

---

## Task 6: Selene heuristic checks

**Files:**
- Create: `src/devrel_origin/core/selene.py`
- Test: `tests/test_selene.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_selene.py`:

```python
"""Selene (SEO) agent tests."""

from datetime import date, datetime, timezone

import pytest

from devrel_origin.core.selene import (
    HeuristicIssue,
    Selene,
    SeoReport,
)
from devrel_origin.tools.seo_crawler import PageProfile


def _profile(**kwargs) -> PageProfile:
    """Build a PageProfile with sensible defaults for tests."""
    defaults = dict(
        url="https://openclaw.ai/", title="A tight 50-character title for OpenClaw",
        title_len=42, meta_description="A reasonable meta description.",
        meta_len=30, h1_count=1,
        h_counts={"h1": 1, "h2": 2, "h3": 0, "h4": 0, "h5": 0, "h6": 0},
        has_schema=True, internal_links_count=5, external_links_count=2,
        word_count=400, crawled_at=datetime.now(timezone.utc).isoformat(),
    )
    defaults.update(kwargs)
    return PageProfile(**defaults)


class TestHeuristics:
    def test_missing_meta_flagged(self):
        p = _profile(meta_description=None, meta_len=0)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "missing_meta" for i in issues)

    def test_overlong_title_flagged(self):
        p = _profile(title="A" * 80, title_len=80)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "title_too_long" for i in issues)

    def test_missing_h1_flagged(self):
        p = _profile(h1_count=0, h_counts={"h1": 0, "h2": 1, "h3": 0, "h4": 0, "h5": 0, "h6": 0})
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "missing_h1" for i in issues)

    def test_duplicate_h1_flagged(self):
        p = _profile(h1_count=3, h_counts={"h1": 3, "h2": 0, "h3": 0, "h4": 0, "h5": 0, "h6": 0})
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "duplicate_h1" for i in issues)

    def test_no_schema_flagged(self):
        p = _profile(has_schema=False)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "no_schema" for i in issues)

    def test_thin_content_flagged(self):
        p = _profile(word_count=80)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "thin_content" for i in issues)

    def test_clean_page_no_issues(self):
        p = _profile()
        issues = Selene._heuristic_issues_for(p)
        assert issues == []
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestHeuristics -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Create `core/selene.py` with heuristic checks**

Create `src/devrel_origin/core/selene.py`:

```python
"""Selene — SEO auditor.

Sitemap-driven crawl + on-page heuristic checks + LLM-driven gap analysis
against Rex's competitor data + GSC keyword performance for decay/opportunity
flagging. Emits Recommendation rows; Mox materializes them as content briefs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from devrel_origin.core.growth import (
    Pillar,
    Recommendation,
    TargetKind,
    persist_recommendation,
)
from devrel_origin.tools.seo_crawler import PageProfile, SEOCrawler

logger = logging.getLogger(__name__)


@dataclass
class HeuristicIssue:
    kind: str        # 'missing_meta'|'title_too_long'|'missing_h1'|'duplicate_h1'|'no_schema'|'thin_content'|'orphan_page'
    page_url: str
    detail: str
    severity: str = "medium"  # 'low'|'medium'|'high'


@dataclass
class KeywordOpportunity:
    keyword: str
    page_url: str
    position: float
    impressions: int
    delta_position: float  # vs prior 30 days; negative = decay
    classification: str    # 'decay'|'opportunity'|'stable'


@dataclass
class GapFinding:
    page_url: str
    missing_topics: list[str]
    missing_entities: list[str]
    suggested_internal_links: list[str]


@dataclass
class SeoReport:
    period_end: str
    profiles: list[PageProfile]
    issues: list[HeuristicIssue]
    keyword_opportunities: list[KeywordOpportunity]
    gap_findings: list[GapFinding]
    recommendations: list[Recommendation] = field(default_factory=list)
    sources_ok: bool = True


class Selene:
    """SEO auditor agent."""

    def __init__(
        self,
        *,
        crawler: SEOCrawler,
        gsc_client: Any,            # GSCClient (Wave 3 Task 2-3)
        llm_client: Any,
        db_path: Path,
        product_url: str,
        product_domain: str,
        gsc_property: Optional[str] = None,
        sitemap_url: Optional[str] = None,
        page_overrides: list[str] | None = None,
        competitors: list[str] | None = None,
    ):
        self.crawler = crawler
        self.gsc = gsc_client
        self.llm = llm_client
        self.db_path = db_path
        self.product_url = product_url
        self.product_domain = product_domain
        self.gsc_property = gsc_property or product_url
        self.sitemap_url = sitemap_url or f"{product_url.rstrip('/')}/sitemap.xml"
        self.page_overrides = page_overrides or []
        self.competitors = competitors or []

    # ──────────────────────────────────────────────────────────────────
    # Heuristic checks
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _heuristic_issues_for(p: PageProfile) -> list[HeuristicIssue]:
        issues: list[HeuristicIssue] = []
        if not p.meta_description or p.meta_len < 50:
            issues.append(HeuristicIssue(
                kind="missing_meta", page_url=p.url,
                detail=f"meta description {'missing' if not p.meta_description else f'only {p.meta_len} chars'}",
                severity="high",
            ))
        if p.title_len > 60:
            issues.append(HeuristicIssue(
                kind="title_too_long", page_url=p.url,
                detail=f"title is {p.title_len} chars (>60)",
                severity="medium",
            ))
        if p.h1_count == 0:
            issues.append(HeuristicIssue(
                kind="missing_h1", page_url=p.url,
                detail="no <h1> on page", severity="high",
            ))
        elif p.h1_count > 1:
            issues.append(HeuristicIssue(
                kind="duplicate_h1", page_url=p.url,
                detail=f"{p.h1_count} <h1> tags on page", severity="medium",
            ))
        if not p.has_schema:
            issues.append(HeuristicIssue(
                kind="no_schema", page_url=p.url,
                detail="no schema.org markup (JSON-LD or microdata)",
                severity="low",
            ))
        if p.word_count < 200:
            issues.append(HeuristicIssue(
                kind="thin_content", page_url=p.url,
                detail=f"only {p.word_count} words", severity="medium",
            ))
        return issues

    def _detect_orphans(self, profiles: list[PageProfile]) -> list[HeuristicIssue]:
        """Pages that no other page links to (zero inbound internal links).

        Builds an inbound-count map from internal_links_count being a
        property of the linking page; for a true inbound count we'd need
        link graph analysis, but for a first cut we use a simpler proxy:
        a page is "orphan" if it doesn't appear in any other page's
        internal_links — we approximate by looking at internal_links_count
        on each page as outbound, and flagging pages with 0 internal_links
        (which means nothing to crawl onward from). For Wave 3, this is
        a placeholder — full link graph in Wave 4 polish.
        """
        return []  # placeholder; full link graph in Wave 4
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestHeuristics -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): heuristic on-page checks (meta, title, h1, schema, thin)"
```

---

## Task 7: GSC trend analysis (decay/opportunity classification)

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_selene.py`:

```python
class TestKeywordTrend:
    def test_decay_flagged_when_position_drops(self):
        rows_now = [
            {"keyword": "k8s monitoring", "page": "https://openclaw.ai/", "position": 12.0,
             "impressions": 2000, "clicks": 30, "ctr": 0.015},
        ]
        rows_prior = [
            {"keyword": "k8s monitoring", "page": "https://openclaw.ai/", "position": 8.0,
             "impressions": 2100, "clicks": 50, "ctr": 0.024},
        ]
        opps = Selene._classify_keyword_trends(rows_now, rows_prior)
        assert len(opps) == 1
        assert opps[0].classification == "decay"
        assert opps[0].delta_position == pytest.approx(4.0)  # got worse

    def test_opportunity_flagged_for_growing_pos5_15(self):
        rows_now = [
            {"keyword": "k8s observability", "page": "https://openclaw.ai/docs",
             "position": 10.0, "impressions": 5000, "clicks": 80, "ctr": 0.016},
        ]
        rows_prior = [
            {"keyword": "k8s observability", "page": "https://openclaw.ai/docs",
             "position": 11.0, "impressions": 3000, "clicks": 50, "ctr": 0.017},
        ]
        opps = Selene._classify_keyword_trends(rows_now, rows_prior)
        assert any(o.classification == "opportunity" for o in opps)

    def test_stable_for_minor_position_change(self):
        rows_now = [
            {"keyword": "openclaw", "page": "https://openclaw.ai/", "position": 1.5,
             "impressions": 5000, "clicks": 1500, "ctr": 0.30},
        ]
        rows_prior = [
            {"keyword": "openclaw", "page": "https://openclaw.ai/", "position": 1.7,
             "impressions": 4900, "clicks": 1450, "ctr": 0.30},
        ]
        opps = Selene._classify_keyword_trends(rows_now, rows_prior)
        assert opps[0].classification == "stable"
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestKeywordTrend -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Implement `_classify_keyword_trends`**

Append to `Selene` class:

```python
    @staticmethod
    def _classify_keyword_trends(
        rows_now: list[dict], rows_prior: list[dict],
    ) -> list[KeywordOpportunity]:
        """Compare two GSC rowsets, classify each keyword as decay|opportunity|stable.

        Decay      = position worsened ≥3 ranks AND impressions stable (within ±20%)
        Opportunity = position 5-15 AND impressions trending UP ≥30%
        Stable      = everything else
        """
        # Index prior rows by (keyword, page)
        prior_idx: dict[tuple[str, str], dict] = {
            (r["keyword"], r.get("page", "")): r for r in rows_prior
        }
        out: list[KeywordOpportunity] = []
        for r in rows_now:
            key = (r["keyword"], r.get("page", ""))
            prior = prior_idx.get(key)
            if prior is None:
                continue
            pos_delta = r["position"] - prior["position"]  # positive = worsened
            imp_now = r.get("impressions", 0) or 1
            imp_prior = prior.get("impressions", 0) or 1
            imp_change = (imp_now - imp_prior) / imp_prior

            if pos_delta >= 3 and abs(imp_change) <= 0.2:
                classification = "decay"
            elif 5 <= r["position"] <= 15 and imp_change >= 0.3:
                classification = "opportunity"
            else:
                classification = "stable"

            out.append(KeywordOpportunity(
                keyword=r["keyword"],
                page_url=r.get("page", ""),
                position=r["position"],
                impressions=r.get("impressions", 0),
                delta_position=pos_delta,
                classification=classification,
            ))
        return out
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestKeywordTrend -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): keyword decay/opportunity classification from GSC trend"
```

---

## Task 8: LLM gap analysis

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_selene.py`:

```python
from unittest.mock import AsyncMock, MagicMock


class TestGapAnalysis:
    @pytest.mark.asyncio
    async def test_gap_analysis_calls_llm_with_competitor_pages(self):
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=(json.dumps({
            "missing_topics": ["distributed tracing setup", "OpenTelemetry export"],
            "missing_entities": ["OpenTelemetry", "Jaeger"],
            "suggested_internal_links": ["/docs/tracing", "/docs/integrations"],
        }), MagicMock()))

        crawler = MagicMock()
        gsc = MagicMock()
        selene = Selene(
            crawler=crawler, gsc_client=gsc, llm_client=llm,
            db_path=Path("/tmp/x.db"),
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
            competitors=["Datadog", "New Relic"],
        )

        finding = await selene._analyze_gap(
            our_page_url="https://openclaw.ai/docs/quickstart",
            our_html="<h1>OpenClaw Quickstart</h1><p>Install via pipx...</p>",
            competitor_pages=[
                ("Datadog", "<h1>Datadog Tracing</h1><p>Auto-instrument...</p>"),
                ("New Relic", "<h1>New Relic Setup</h1><p>Use OTLP...</p>"),
            ],
            target_keyword="kubernetes observability",
        )
        assert "OpenTelemetry" in finding.missing_entities
        assert "/docs/tracing" in finding.suggested_internal_links
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestGapAnalysis -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add `_analyze_gap`**

Append to `Selene` class:

```python
import json  # ensure at top of file

_GAP_PROMPT = """You are an SEO content analyst. Compare our page against competitor pages on the same query.

Our page (truncated to 4KB):
URL: {our_url}
Target keyword: {target_keyword}

{our_html}

Competitor pages (each 2KB):

{competitor_blocks}

Identify what our page is MISSING that competitors include. Return JSON only:

{{
  "missing_topics": ["<topic 1>", "<topic 2>"],   // subjects competitors cover that we don't
  "missing_entities": ["<entity>"],                // products/concepts we don't name-drop
  "suggested_internal_links": ["/path1"]           // internal pages to link from this one
}}

Be specific. Don't list things we already cover. Return ONLY JSON, no markdown fences."""


    async def _analyze_gap(
        self,
        *,
        our_page_url: str,
        our_html: str,
        competitor_pages: list[tuple[str, str]],
        target_keyword: str,
    ) -> GapFinding:
        comp_blocks = "\n\n".join(
            f"### {name}\n\n{html[:2000]}"
            for name, html in competitor_pages
        )
        prompt = _GAP_PROMPT.format(
            our_url=our_page_url,
            target_keyword=target_keyword,
            our_html=our_html[:4000],
            competitor_blocks=comp_blocks or "(none)",
        )
        text, _ = await self.llm.generate(
            system_prompt="You are an SEO content analyst.",
            user_prompt=prompt,
            temperature=0.2,
            max_tokens=800,
        )
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)
        return GapFinding(
            page_url=our_page_url,
            missing_topics=list(data.get("missing_topics", []))[:10],
            missing_entities=list(data.get("missing_entities", []))[:10],
            suggested_internal_links=list(data.get("suggested_internal_links", []))[:5],
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestGapAnalysis -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): LLM gap analysis vs competitor pages"
```

---

## Task 9: Selene persistence (SEO recommendations + fact tables)

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_selene.py`:

```python
import sqlite3
from devrel_origin.project import state


@pytest.fixture
def init_db(tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (id, period_end, generated_at, body_json) "
            "VALUES (?, ?, datetime('now'), '{}')",
            ("test-report", "2026-04-01"),
        )
        conn.commit()
    return db


class TestPersist:
    def test_persist_writes_keyword_metrics(self, init_db, tmp_path):
        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        gsc_rows = [
            {"keyword": "k8s monitoring", "page": "https://openclaw.ai/",
             "position": 12.0, "impressions": 2000, "clicks": 30, "ctr": 0.015},
        ]
        selene._persist_keyword_metrics(gsc_rows, period_end="2026-04-01")
        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT keyword, page_url, position, impressions FROM seo_keyword_metrics"
            )
            rows = cur.fetchall()
        assert rows == [("k8s monitoring", "https://openclaw.ai/", 12.0, 2000)]

    def test_persist_writes_page_profiles(self, init_db, tmp_path):
        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        profiles = [_profile()]
        selene._persist_page_profiles(profiles, period_end="2026-04-01")
        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT page_url, title_len, h1_count, has_schema FROM seo_page_profiles"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "https://openclaw.ai/"
        assert rows[0][3] == 1  # has_schema=True stored as 1

    def test_persist_recommendations_emits_per_issue(self, init_db, tmp_path):
        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        report = SeoReport(
            period_end="2026-04-01",
            profiles=[_profile()],
            issues=[
                HeuristicIssue(kind="missing_meta", page_url="https://openclaw.ai/", detail="..."),
            ],
            keyword_opportunities=[
                KeywordOpportunity(
                    keyword="k8s observability", page_url="https://openclaw.ai/docs",
                    position=10.0, impressions=5000,
                    delta_position=-1.0, classification="opportunity",
                ),
            ],
            gap_findings=[
                GapFinding(
                    page_url="https://openclaw.ai/docs/quickstart",
                    missing_topics=["distributed tracing"], missing_entities=["OpenTelemetry"],
                    suggested_internal_links=["/docs/tracing"],
                ),
            ],
        )
        selene._persist_recommendations(report, report_id="test-report")
        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT action, target, target_kind FROM analytics_recommendations "
                "WHERE pillar = 'seo'"
            )
            rows = cur.fetchall()
        assert any(r[0] == "investigate" and r[2] == "url" for r in rows)
        assert any(r[0] == "amplify" and r[2] == "keyword" for r in rows)
        assert any(r[0] == "rewrite" and r[2] == "url" for r in rows)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestPersist -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the persist methods**

Append to `Selene` class:

```python
    def _persist_keyword_metrics(
        self, rows: list[dict], *, period_end: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            for r in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO seo_keyword_metrics
                        (keyword, page_url, period_end, position, ctr, impressions, clicks)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["keyword"], r.get("page", ""), period_end,
                        r.get("position"), r.get("ctr"),
                        r.get("impressions"), r.get("clicks"),
                    ),
                )
            conn.commit()

    def _persist_page_profiles(
        self, profiles: list[PageProfile], *, period_end: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            for p in profiles:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO seo_page_profiles
                        (page_url, period_end, title_len, meta_len, h1_count,
                         word_count, has_schema, internal_links, crawled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.url, period_end,
                        p.title_len, p.meta_len, p.h1_count,
                        p.word_count, 1 if p.has_schema else 0,
                        p.internal_links_count, p.crawled_at,
                    ),
                )
            conn.commit()

    def _persist_recommendations(
        self, report: SeoReport, *, report_id: str,
    ) -> None:
        # Heuristic issues → investigate × url (high severity → confidence 0.8)
        sev_to_conf = {"low": 0.4, "medium": 0.6, "high": 0.8}
        for issue in report.issues:
            rec = Recommendation(
                pillar=Pillar.SEO,
                action="investigate",
                target=issue.page_url,
                target_kind=TargetKind.URL,
                confidence=sev_to_conf.get(issue.severity, 0.5),
                source_ids=[issue.kind],
                first_seen_period=report.period_end,
            )
            persist_recommendation(self.db_path, report_id, rec)
            report.recommendations.append(rec)

        # Keyword opportunities → amplify × keyword
        for opp in report.keyword_opportunities:
            if opp.classification == "opportunity":
                rec = Recommendation(
                    pillar=Pillar.SEO,
                    action="amplify",
                    target=opp.keyword,
                    target_kind=TargetKind.KEYWORD,
                    confidence=0.7,
                    source_ids=[opp.page_url],
                    first_seen_period=report.period_end,
                )
                persist_recommendation(self.db_path, report_id, rec)
                report.recommendations.append(rec)
            elif opp.classification == "decay":
                rec = Recommendation(
                    pillar=Pillar.SEO,
                    action="rewrite",
                    target=opp.page_url,
                    target_kind=TargetKind.URL,
                    confidence=0.65,
                    source_ids=[opp.keyword],
                    first_seen_period=report.period_end,
                )
                persist_recommendation(self.db_path, report_id, rec)
                report.recommendations.append(rec)

        # Gap findings → rewrite × url
        for gap in report.gap_findings:
            rec = Recommendation(
                pillar=Pillar.SEO,
                action="rewrite",
                target=gap.page_url,
                target_kind=TargetKind.URL,
                confidence=0.7,
                source_ids=gap.missing_topics + gap.missing_entities,
                first_seen_period=report.period_end,
            )
            persist_recommendation(self.db_path, report_id, rec)
            report.recommendations.append(rec)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestPersist -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): persist keyword + page metrics + Recommendations"
```

---

## Task 10: Selene brief generation + `execute()`

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_selene.py`:

```python
class TestBriefAndExecute:
    def test_write_briefs_creates_one_per_recommendation(self, init_db, tmp_path):
        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        report = SeoReport(
            period_end="2026-04-01",
            profiles=[_profile()],
            issues=[],
            keyword_opportunities=[],
            gap_findings=[],
            recommendations=[
                Recommendation(
                    pillar=Pillar.SEO, action="rewrite", target="https://openclaw.ai/docs",
                    target_kind=TargetKind.URL, confidence=0.7,
                    source_ids=["distributed tracing", "OpenTelemetry"],
                    first_seen_period="2026-04-01",
                ),
            ],
        )
        deliverables_dir = tmp_path / "deliverables"
        selene._write_briefs(report, deliverables_dir)
        files = list(deliverables_dir.glob("seo-brief-*.md"))
        assert len(files) == 1
        text = files[0].read_text()
        assert "/docs" in text
        assert "OpenTelemetry" in text
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestBriefAndExecute -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add `_write_briefs` and `execute`**

Append to `Selene` class:

```python
    def _write_briefs(self, report: SeoReport, deliverables_dir: Path) -> None:
        deliverables_dir.mkdir(parents=True, exist_ok=True)
        for rec in report.recommendations:
            md_lines = [
                f"# Selene brief: {rec.action} `{rec.target}`",
                "",
                f"**Period:** {report.period_end}",
                f"**Pillar:** seo",
                f"**Target kind:** {rec.target_kind.value}",
                f"**Confidence:** {rec.confidence:.2f}",
                "",
            ]
            if rec.source_ids:
                md_lines.extend([
                    "## Why",
                    "",
                ])
                md_lines.extend(f"- {sid}" for sid in rec.source_ids)
                md_lines.append("")

            md_lines.extend(["## Suggested next steps", ""])
            if rec.action == "rewrite":
                md_lines.append(f"- Kai: rewrite `{rec.target}` to address the items above.")
            elif rec.action == "amplify":
                md_lines.append(f"- Mox: produce a piece of content targeting the keyword `{rec.target}`.")
            elif rec.action == "investigate":
                md_lines.append(f"- Manual: review `{rec.target}` and address the flagged technical issue.")

            slug = rec.target.replace("https://", "").replace("/", "-").replace(" ", "-")[:60]
            path = deliverables_dir / f"seo-brief-{report.period_end}-{rec.action}-{slug}.md"
            path.write_text("\n".join(md_lines) + "\n")

    async def execute(
        self,
        *,
        period_end: str,
        report_id: str,
        rex_competitive_html: dict[str, dict[str, str]] | None = None,
        deliverables_dir: Path | None = None,
    ) -> SeoReport:
        """Run a full Selene cycle: crawl → heuristics → GSC trend → gap analysis →
        persist → brief.

        `rex_competitive_html` is `{competitor_name: {url: html}}` from
        Rex's competitive intel run; gap analysis uses the first 3 entries
        per competitor.
        """
        rex_competitive_html = rex_competitive_html or {}

        # 1. Crawl
        try:
            sitemap = await self.crawler.fetch_sitemap(self.sitemap_url)
        except Exception as e:
            logger.warning(f"Selene: sitemap fetch failed: {e}")
            return SeoReport(
                period_end=period_end, profiles=[], issues=[],
                keyword_opportunities=[], gap_findings=[], sources_ok=False,
            )

        urls = [e.url for e in sitemap]
        if self.page_overrides:
            urls = self.page_overrides

        profiles: list[PageProfile] = []
        for url in urls:
            p = await self.crawler.fetch_and_parse(url)
            if p is not None:
                profiles.append(p)

        # 2. Heuristic issues
        issues: list[HeuristicIssue] = []
        for p in profiles:
            issues.extend(self._heuristic_issues_for(p))

        # 3. GSC trend
        keyword_opportunities: list[KeywordOpportunity] = []
        try:
            now = date.fromisoformat(period_end)
            rows_now = await self.gsc.search_analytics_query(
                site_url=self.gsc_property,
                start=now - timedelta(days=30), end=now,
            )
            rows_prior = await self.gsc.search_analytics_query(
                site_url=self.gsc_property,
                start=now - timedelta(days=60), end=now - timedelta(days=30),
            )
            self._persist_keyword_metrics(rows_now, period_end=period_end)
            keyword_opportunities = self._classify_keyword_trends(rows_now, rows_prior)
        except Exception as e:
            logger.warning(f"Selene: GSC fetch failed: {e}")

        # 4. Gap analysis on top-3-impression pages (cheap budget)
        gap_findings: list[GapFinding] = []
        top_pages = sorted(
            keyword_opportunities, key=lambda o: o.impressions, reverse=True,
        )[:3]
        for opp in top_pages:
            our_html = ""
            for p in profiles:
                if p.url == opp.page_url:
                    # Reload from cache if available
                    if self.crawler.cache_dir is not None:
                        slug = (urlparse(p.url).path.replace("/", "_") or "_root")[:80]
                        cache_file = self.crawler.cache_dir / f"{slug}.html"
                        if cache_file.is_file():
                            our_html = cache_file.read_text()
                    break

            competitor_pages: list[tuple[str, str]] = []
            for comp_name, urls_map in (rex_competitive_html or {}).items():
                for u, html in (urls_map or {}).items():
                    competitor_pages.append((comp_name, html))
                    if len(competitor_pages) >= 3:
                        break
                if len(competitor_pages) >= 3:
                    break

            try:
                gap = await self._analyze_gap(
                    our_page_url=opp.page_url,
                    our_html=our_html,
                    competitor_pages=competitor_pages,
                    target_keyword=opp.keyword,
                )
                gap_findings.append(gap)
            except Exception as e:
                logger.warning(f"Selene: gap analysis failed for {opp.page_url}: {e}")

        # 5. Persist + brief
        report = SeoReport(
            period_end=period_end, profiles=profiles, issues=issues,
            keyword_opportunities=keyword_opportunities, gap_findings=gap_findings,
        )
        self._persist_page_profiles(profiles, period_end=period_end)
        self._persist_recommendations(report, report_id=report_id)
        if deliverables_dir is not None:
            self._write_briefs(report, deliverables_dir)

        return report
```

(Add `from urllib.parse import urlparse` at the top of the file.)

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_selene.py -v --no-cov
pytest tests/ -q --no-header
```

Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): brief generation + Selene.execute end-to-end"
```

---

## Task 11: `cli/seo.py` — `connect-gsc` + `crawl` verbs

**Files:**
- Create: `src/devrel_origin/cli/seo.py`
- Modify: `src/devrel_origin/cli/__init__.py`
- Test: `tests/cli/test_seo_command.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_seo_command.py`:

```python
"""CLI smoke tests for `devrel seo ...`."""

from typer.testing import CliRunner

from devrel_origin.cli import app


def test_seo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "--help"])
    assert result.exit_code == 0
    for verb in ("connect-gsc", "crawl", "report", "history", "diff", "calibration"):
        assert verb in result.output.lower()


def test_seo_connect_gsc_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "connect-gsc", "--help"])
    assert result.exit_code == 0


def test_seo_crawl_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "crawl", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_seo_command.py -v --no-cov
```

Expected: `seo` not registered.

- [ ] **Step 3: Create `cli/seo.py` skeleton with `connect-gsc` and `crawl`**

Create `src/devrel_origin/cli/seo.py`:

```python
"""`devrel seo ...` — SEO auditor verbs (Selene)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.core.growth.target_kinds import Pillar
from devrel_origin.core.llm import LLMClient
from devrel_origin.core.selene import Selene
from devrel_origin.tools.gsc_client import GSCClient, OAuthError
from devrel_origin.tools.seo_crawler import SEOCrawler

seo_app = typer.Typer(
    name="seo",
    help="SEO auditor (Selene). Crawl + LLM gap analysis + Google Search Console.",
    no_args_is_help=True,
)

_console = Console()


def _build_selene(paths) -> Selene:
    cfg = paths.config
    seo_cfg = cfg.get("seo", {}) or {}
    growth_cfg = cfg.get("growth", {}) or {}

    crawler = SEOCrawler(
        crawl_delay_ms=int(seo_cfg.get("crawl_delay_ms", 1000)),
        max_pages=int(seo_cfg.get("max_crawl_pages", 200)),
        cache_dir=paths.devrel_dir / "seo" / "crawls",
    )
    gsc = GSCClient(creds_path=paths.devrel_dir / "credentials" / "gsc.json")
    # PSI client wired for the Multi-Surface upgrade (Task 16). Free tier;
    # cached 30d on disk so we don't re-call on every cycle.
    from devrel_origin.tools.psi_client import PsiClient
    psi = PsiClient(cache_dir=paths.devrel_dir / "seo" / "psi-cache", cache_ttl_days=30)
    return Selene(
        crawler=crawler, gsc_client=gsc, llm_client=LLMClient.from_env(),
        db_path=paths.devrel_dir / "state.db",
        product_url=cfg.get("product_url", ""),
        product_domain=cfg.get("product_domain", ""),
        gsc_property=seo_cfg.get("gsc_property", "") or cfg.get("product_url", ""),
        page_overrides=growth_cfg.get("seo_pages", []) or [],
        competitors=growth_cfg.get("seo_competitors", []) or [],
        psi_client=psi,
    )


@seo_app.command("connect-gsc")
def connect_gsc() -> None:
    """Run the GSC OAuth flow. Opens a browser; saves token to .devrel/credentials/gsc.json."""
    paths = find_paths_or_exit()
    creds_path = paths.devrel_dir / "credentials" / "gsc.json"
    client = GSCClient(creds_path=creds_path)
    try:
        client.connect()
    except OAuthError as e:
        _console.print(f"[red]OAuth failed:[/red] {e}")
        raise typer.Exit(code=2)
    _console.print(f"[green]Connected. Credentials at {creds_path}.[/green]")
    _console.print(
        "Set `[seo].gsc_property` in `.devrel/config.toml` to the verified "
        "Search Console property URL (e.g. `https://openclaw.ai/`)."
    )


@seo_app.command("crawl")
def crawl(
    no_cache: bool = typer.Option(False, "--no-cache", help="Skip HTML caching"),
) -> None:
    """Walk the sitemap and parse pages. Writes profiles to seo_page_profiles."""
    paths = find_paths_or_exit()
    selene = _build_selene(paths)
    if no_cache:
        selene.crawler.cache_dir = None

    async def _run():
        sitemap = await selene.crawler.fetch_sitemap(selene.sitemap_url)
        profiles = []
        for entry in sitemap[:selene.crawler.max_pages]:
            p = await selene.crawler.fetch_and_parse(entry.url)
            if p is not None:
                profiles.append(p)
        return profiles

    profiles = asyncio.run(_run())
    table = Table(title=f"Selene crawl — {len(profiles)} pages")
    table.add_column("URL", style="cyan", overflow="fold")
    table.add_column("Title len", justify="right")
    table.add_column("Meta len", justify="right")
    table.add_column("H1s", justify="right")
    table.add_column("Words", justify="right")
    table.add_column("Schema", justify="right")
    for p in profiles[:30]:
        table.add_row(
            p.url, str(p.title_len), str(p.meta_len),
            str(p.h1_count), str(p.word_count),
            "✓" if p.has_schema else "✗",
        )
    _console.print(table)
    period_end = date.today().isoformat()
    selene._persist_page_profiles(profiles, period_end=period_end)
    _console.print(f"[green]Persisted {len(profiles)} profiles for {period_end}.[/green]")
```

Update `src/devrel_origin/cli/__init__.py`:

```python
from devrel_origin.cli.seo import seo_app
# ...
app.add_typer(seo_app, name="seo")
```

- [ ] **Step 4: Run tests**

For the help-list test, adjust to only check for `connect-gsc`+`crawl` until `report`/`history`/etc. land in Task 12:

```python
def test_seo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "--help"])
    assert result.exit_code == 0
    for verb in ("connect-gsc", "crawl"):
        assert verb in result.output.lower()
```

```bash
pytest tests/cli/test_seo_command.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/seo.py src/devrel_origin/cli/__init__.py tests/cli/test_seo_command.py
git commit -m "feat(cli): devrel seo {connect-gsc,crawl}"
```

---

## Task 12: `cli/seo.py` — `report` + `history` + `diff` + `calibration`

**Files:**
- Modify: `src/devrel_origin/cli/seo.py`
- Modify: `tests/cli/test_seo_command.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/cli/test_seo_command.py`:

```python
def test_seo_report_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "report", "--help"])
    assert result.exit_code == 0


def test_seo_history_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "history", "openclaw"])
    assert result.exit_code == 0


def test_seo_calibration_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "calibration"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_seo_command.py -v --no-cov
```

Expected: 3 FAIL.

- [ ] **Step 3: Add the verbs**

Append to `cli/seo.py`:

```python
@seo_app.command("report")
def report(
    since: str = typer.Option("30d", "--since"),
    push: bool = typer.Option(False, "--push"),
    format: str = typer.Option("markdown", "--format"),
) -> None:
    """Run a full Selene cycle (crawl + GSC + LLM gap analysis + persist)."""
    paths = find_paths_or_exit()
    selene = _build_selene(paths)
    period_end = date.today().isoformat()
    report_id = f"seo-{period_end}"

    async def _run():
        return await selene.execute(
            period_end=period_end, report_id=report_id,
            rex_competitive_html={},  # Atlas Stage 5c will pass Rex's data; manual run skips it
            deliverables_dir=paths.devrel_dir / "deliverables",
        )

    result = asyncio.run(_run())

    if format == "json":
        _console.print(json.dumps({
            "period_end": result.period_end,
            "n_profiles": len(result.profiles),
            "n_issues": len(result.issues),
            "n_keyword_opportunities": len(result.keyword_opportunities),
            "n_gap_findings": len(result.gap_findings),
            "n_recommendations": len(result.recommendations),
        }, indent=2))
        return

    table = Table(title=f"Selene report — {period_end}")
    table.add_column("Section", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Pages crawled", str(len(result.profiles)))
    table.add_row("Heuristic issues", str(len(result.issues)))
    table.add_row("Decay/opportunity keywords", str(len(result.keyword_opportunities)))
    table.add_row("Gap findings", str(len(result.gap_findings)))
    table.add_row("Recommendations", str(len(result.recommendations)))
    _console.print(table)


@seo_app.command("history")
def history(
    keyword: str = typer.Argument(..., help="Keyword to track"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Position trajectory for one keyword across reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"SEO history — {keyword}")
    table.add_column("Period", style="cyan")
    table.add_column("Page", overflow="fold")
    table.add_column("Position", justify="right")
    table.add_column("Impressions", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT period_end, page_url, position, impressions
            FROM seo_keyword_metrics WHERE keyword = ?
            ORDER BY period_end DESC LIMIT ?
            """,
            (keyword, limit),
        )
        for period_end, page_url, position, impressions in cur:
            table.add_row(
                period_end, page_url,
                f"{position:.1f}" if position else "-",
                f"{impressions:,}" if impressions else "-",
            )
    _console.print(table)


@seo_app.command("diff")
def diff(
    period_a: str = typer.Argument(...),
    period_b: str = typer.Argument(...),
) -> None:
    """Per-keyword position delta between two SEO reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"SEO diff — {period_a} → {period_b}")
    table.add_column("Keyword", style="cyan")
    table.add_column(period_a, justify="right")
    table.add_column(period_b, justify="right")
    table.add_column("Δ pos", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT a.keyword, AVG(a.position), AVG(b.position)
            FROM seo_keyword_metrics a
            JOIN seo_keyword_metrics b ON a.keyword = b.keyword
            WHERE a.period_end = ? AND b.period_end = ?
            GROUP BY a.keyword
            ORDER BY ABS(AVG(b.position) - AVG(a.position)) DESC
            LIMIT 30
            """,
            (period_a, period_b),
        )
        for keyword, pos_a, pos_b in cur:
            delta = (pos_b or 0) - (pos_a or 0)
            table.add_row(
                keyword, f"{pos_a:.1f}", f"{pos_b:.1f}", f"{delta:+.1f}",
            )
    _console.print(table)


@seo_app.command("calibration")
def calibration() -> None:
    """Score historical SEO recommendations against subsequent keyword data."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    from devrel_origin.core.growth.recommendations import calibrate

    def _score_outcome(rec) -> str:
        if rec.applied_at is None:
            return "unchanged"
        # For amplify × keyword: did position improve afterward?
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                """
                SELECT period_end, AVG(position) FROM seo_keyword_metrics
                WHERE keyword = ? AND period_end >= ?
                GROUP BY period_end ORDER BY period_end LIMIT 2
                """,
                (rec.target, rec.applied_at[:10]),
            )
            rows = cur.fetchall()
        if len(rows) < 2:
            return "unchanged"
        # Lower position = better; improved = decreased
        return "improved" if rows[1][1] < rows[0][1] else (
            "regressed" if rows[1][1] > rows[0][1] else "unchanged"
        )

    result = calibrate(db_path, Pillar.SEO, outcome_scorer=_score_outcome)
    if not result:
        _console.print("[yellow]No applied SEO recommendations yet.[/yellow]")
        return

    table = Table(title="SEO calibration")
    table.add_column("Action", style="cyan")
    table.add_column("Applied", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Lift vs coinflip", justify="right")
    for action, stats in result.items():
        table.add_row(
            action, str(stats["applied_count"]),
            f"{stats['hit_rate']:.1%}", f"{stats['lift_vs_coinflip']:+.1%}",
        )
    _console.print(table)
```

- [ ] **Step 4: Restore full help-list assert + run tests**

In `tests/cli/test_seo_command.py`, restore the full check:

```python
def test_seo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["seo", "--help"])
    assert result.exit_code == 0
    for verb in ("connect-gsc", "crawl", "report", "history", "diff", "calibration"):
        assert verb in result.output.lower()
```

```bash
pytest tests/cli/test_seo_command.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/cli/seo.py tests/cli/test_seo_command.py
git commit -m "feat(cli): devrel seo {report,history,diff,calibration}"
```

---

## Task 13: Atlas Stage 5c registration (Selene) + export Selene

**Files:**
- Modify: `src/devrel_origin/core/atlas.py`
- Modify: `src/devrel_origin/core/__init__.py`
- Test: `tests/test_atlas.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_atlas.py`:

```python
@pytest.mark.asyncio
async def test_atlas_runs_selene_when_seo_in_run_enabled(tmp_path, monkeypatch):
    """Stage 5c — when seo_in_run=true, Atlas calls Selene.execute."""
    # ... boilerplate matching the test_atlas_runs_cyra and _vega patterns
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_atlas.py -k "selene" -v --no-cov
```

Expected: AssertionError.

- [ ] **Step 3: Wire Selene + export**

In `src/devrel_origin/core/atlas.py`, after the Vega block from Wave 2, add:

```python
if self.config.orchestration.seo_in_run:
    try:
        selene = self._build_selene()
        seo_report = await selene.execute(
            period_end=self.context.week_of,
            report_id=f"seo-{self.context.week_of}",
            rex_competitive_html=self._extract_rex_competitive_html(),
            deliverables_dir=self.project_paths.devrel_dir / "deliverables",
        )
        self.context.seo_report = {
            "period_end": seo_report.period_end,
            "n_recommendations": len(seo_report.recommendations),
            "n_issues": len(seo_report.issues),
            "n_gap_findings": len(seo_report.gap_findings),
        }
    except Exception as e:
        logger.warning(f"Atlas Stage 5c (Selene) failed: {e}")
        self.context.seo_report = {"error": str(e)}
```

Add the helpers:

```python
    def _build_selene(self):
        from devrel_origin.core.selene import Selene
        from devrel_origin.tools.gsc_client import GSCClient
        from devrel_origin.tools.psi_client import PsiClient
        from devrel_origin.tools.seo_crawler import SEOCrawler

        seo_cfg = getattr(self.config, "seo", {}) or {}
        if not isinstance(seo_cfg, dict):
            seo_cfg = seo_cfg.__dict__
        growth_cfg = getattr(self.config, "growth", {}) or {}
        if not isinstance(growth_cfg, dict):
            growth_cfg = growth_cfg.__dict__

        crawler = SEOCrawler(
            crawl_delay_ms=int(seo_cfg.get("crawl_delay_ms", 1000)),
            max_pages=int(seo_cfg.get("max_crawl_pages", 200)),
            cache_dir=self.project_paths.devrel_dir / "seo" / "crawls",
        )
        gsc = GSCClient(
            creds_path=self.project_paths.devrel_dir / "credentials" / "gsc.json",
        )
        psi = PsiClient(
            cache_dir=self.project_paths.devrel_dir / "seo" / "psi-cache",
            cache_ttl_days=30,
        )
        return Selene(
            crawler=crawler, gsc_client=gsc, llm_client=self.llm,
            db_path=self.project_paths.devrel_dir / "state.db",
            product_url=self.config.product_url,
            product_domain=getattr(self.config, "product_domain", ""),
            gsc_property=seo_cfg.get("gsc_property", "") or self.config.product_url,
            page_overrides=growth_cfg.get("seo_pages", []) or [],
            competitors=growth_cfg.get("seo_competitors", []) or [],
            psi_client=psi,
        )

    def _extract_rex_competitive_html(self) -> dict:
        """Pull cached competitor HTML from Rex's competitive output (when present)."""
        rex = self.context.rex_competitive or {}
        if isinstance(rex, dict):
            return rex.get("competitor_html_by_url", {}) or {}
        return {}
```

Add `seo_report: dict = field(default_factory=dict)` to `SharedContext`.

In `src/devrel_origin/core/__init__.py`, add `Selene` to imports + `__all__`.

- [ ] **Step 4: Run tests + full gate**

```bash
pytest tests/test_atlas.py -k "selene" -v --no-cov
pytest tests/ -q --no-header
ruff check . && ruff format --check . | tail -1
rm -rf dist/ build/ && python -m build 2>&1 | tail -2
python -m twine check dist/* 2>&1 | tail -2
```

Expected: all PASS; full suite green; ruff clean; build clean.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/atlas.py src/devrel_origin/core/__init__.py tests/test_atlas.py
git commit -m "feat(atlas): Stage 5c (Selene) gated by seo_in_run config + export Selene"
```

---

## Multi-Surface upgrade (added 2026-05-06)

Spec Section 3.1 was expanded after this plan was written: Selene becomes the
**Multi-Surface Search auditor**, adds `llms.txt`/AI-bot directive checks +
PageSpeed Insights API for INP/LCP + typed-schema inventory + cross-pillar
reads of Vega's `geo_visibility`. Tasks 14-19 below add this without
rewriting Tasks 1-13 in place. Run them AFTER their referenced base task is
green; integration instructions on each task explain the touch-points.

**Task ordering** when executing this plan:

```
Tasks 1-3 (oauth_constants + GSC client)        — unchanged
Task 4 (sitemap walker + robots.txt)            — unchanged
  ↓
Task 14 (extend crawler: llms.txt + AI bots)    — NEW
  ↓
Task 5 (page parser)                            — apply Task 15 inline edits
Task 15 (PageProfile extension fields)          — NEW; modifies Task 5 dataclass + parser
  ↓
Task 16 (PageSpeed Insights client)             — NEW; tools/psi_client.py
  ↓
Task 6 (heuristic checks)                       — apply Task 17 inline edits
Task 17 (Multi-Surface heuristics)              — NEW; modifies Task 6's _heuristic_issues_for
  ↓
Task 7 (GSC trend)                              — unchanged
  ↓
Task 8 (LLM gap analysis)                       — apply Task 18 inline edits
Task 18 (reframed gap-analysis prompt)          — NEW; replaces _GAP_PROMPT in Task 8
  ↓
Task 19 (Multi-Surface cross-pillar read)       — NEW; modifies Selene.execute()
  ↓
Tasks 9-13                                      — apply Task 20 inline edits
Task 20 (persist + brief integration)           — NEW; touches _persist_page_profiles
                                                  and _write_briefs
```

Total added budget: 2 days (matches the spec's 6 → 8 day Wave 3 update).

---

## Task 14: Extend crawler with `llms.txt` parsing + AI-bot directive checks

**Files:**
- Modify: `src/devrel_origin/tools/seo_crawler.py`
- Modify: `tests/test_seo_crawler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_seo_crawler.py`:

```python
@respx.mock
@pytest.mark.asyncio
async def test_robots_recognizes_ai_bot_directives(crawler):
    respx.get("https://openclaw.ai/robots.txt").mock(
        return_value=Response(200, text=(
            "User-agent: *\nAllow: /\n\n"
            "User-agent: OAI-SearchBot\nDisallow: /\n\n"
            "User-agent: PerplexityBot\nAllow: /docs\n"
        ))
    )
    is_general = await crawler.is_allowed("https://openclaw.ai/", user_agent="*")
    is_oai = await crawler.is_allowed("https://openclaw.ai/", user_agent="OAI-SearchBot")
    is_pplx = await crawler.is_allowed("https://openclaw.ai/docs/x", user_agent="PerplexityBot")
    assert is_general is True
    assert is_oai is False  # explicitly disallowed
    assert is_pplx is True


@respx.mock
@pytest.mark.asyncio
async def test_fetch_llms_txt_returns_directives(crawler):
    llms_text = (
        "# AI agents may use this content for retrieval\n"
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "# Recommended pages\n"
        "Recommend: /docs/quickstart\n"
        "Recommend: /docs/architecture\n"
    )
    respx.get("https://openclaw.ai/llms.txt").mock(
        return_value=Response(200, text=llms_text, headers={"content-type": "text/plain"})
    )
    out = await crawler.fetch_llms_txt("https://openclaw.ai/llms.txt")
    assert out is not None
    assert out.is_present is True
    assert "/docs/quickstart" in out.recommended_pages


@respx.mock
@pytest.mark.asyncio
async def test_fetch_llms_txt_returns_none_when_404(crawler):
    respx.get("https://openclaw.ai/llms.txt").mock(return_value=Response(404))
    out = await crawler.fetch_llms_txt("https://openclaw.ai/llms.txt")
    assert out is None or out.is_present is False
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_seo_crawler.py -k "ai_bot_directives or llms_txt" -v --no-cov
```

Expected: AttributeError on `fetch_llms_txt`; the AI-bot directive test may
already pass (RobotFileParser handles per-user-agent rules natively).

- [ ] **Step 3: Add `LlmsTxt` dataclass + `fetch_llms_txt`**

Append to `src/devrel_origin/tools/seo_crawler.py`:

```python
@dataclass
class LlmsTxt:
    """Parsed `<host>/llms.txt` per llmstxt.org spec."""

    is_present: bool
    user_agent_rules: dict[str, list[str]]   # {'*': ['Allow: /'], ...}
    recommended_pages: list[str]             # 'Recommend:' lines


# Add this method to SEOCrawler:
class SEOCrawler:
    # ... existing methods ...

    async def fetch_llms_txt(self, llms_url: str) -> Optional[LlmsTxt]:
        """Fetch and parse `/llms.txt` if present.

        Returns None on 404 or parse error; an `is_present=False` `LlmsTxt`
        object would be ambiguous, so absence is `None`.
        """
        client = await self._ensure_client()
        try:
            resp = await client.get(llms_url)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None

        ua_rules: dict[str, list[str]] = {}
        recommended: list[str] = []
        current_ua: str | None = None
        for line in resp.text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.lower().startswith("user-agent:"):
                current_ua = stripped.split(":", 1)[1].strip()
                ua_rules.setdefault(current_ua, [])
            elif stripped.lower().startswith("recommend:"):
                recommended.append(stripped.split(":", 1)[1].strip())
            elif current_ua is not None:
                ua_rules[current_ua].append(stripped)

        return LlmsTxt(
            is_present=True,
            user_agent_rules=ua_rules,
            recommended_pages=recommended,
        )
```

The existing `RobotFileParser`-based `is_allowed` already handles per-user-agent rules
correctly when the user-agent is passed in (Python's `urllib.robotparser` matches
specific user-agent stanzas first). No code change needed for AI-bot directives —
just expose them through callers.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_seo_crawler.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/seo_crawler.py tests/test_seo_crawler.py
git commit -m "feat(seo): llms.txt parser + AI-bot directive support"
```

---

## Task 15: Extend `PageProfile` with schema types + Core Web Vitals + redirect chain

**Files:**
- Modify: `src/devrel_origin/tools/seo_crawler.py`
- Modify: `tests/test_seo_crawler.py`

This task amends Task 5's `PageProfile` dataclass. New fields:
- `schema_types: list[str]` — detected schema.org types (e.g. `["Organization", "SoftwareApplication"]`)
- `inp_ms: int | None` — Interaction to Next Paint, set later by PSI (Task 16)
- `lcp_ms: int | None` — Largest Contentful Paint
- `redirect_chain_len: int` — number of redirects before reaching the URL

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_seo_crawler.py`:

```python
class TestPageProfileExtended:
    @pytest.mark.asyncio
    async def test_parse_extracts_schema_types(self, crawler):
        html = """
        <html><head>
            <script type="application/ld+json">
              {"@type": "SoftwareApplication", "name": "OpenClaw"}
            </script>
            <script type="application/ld+json">
              {"@type": "Organization", "name": "OpenClaw Inc",
               "sameAs": ["https://github.com/openclaw"]}
            </script>
        </head><body><h1>OpenClaw</h1></body></html>
        """
        profile = await crawler.parse_page(html, page_url="https://openclaw.ai/")
        assert "SoftwareApplication" in profile.schema_types
        assert "Organization" in profile.schema_types
        assert profile.has_schema is True

    @pytest.mark.asyncio
    async def test_parse_when_no_schema(self, crawler):
        html = "<html><body><h1>Plain</h1></body></html>"
        profile = await crawler.parse_page(html, page_url="https://e.com/")
        assert profile.schema_types == []
        assert profile.has_schema is False

    @pytest.mark.asyncio
    async def test_inp_lcp_default_to_none(self, crawler):
        html = "<html><body><h1>X</h1></body></html>"
        profile = await crawler.parse_page(html, page_url="https://e.com/")
        # PSI fields populated later by psi_client; parse_page leaves them None
        assert profile.inp_ms is None
        assert profile.lcp_ms is None

    @pytest.mark.asyncio
    async def test_redirect_chain_len_recorded(self, crawler, respx_mock):
        # Set up a redirect chain: /a -> /b -> /c (final)
        respx_mock.get("https://e.com/a").mock(
            return_value=Response(301, headers={"location": "https://e.com/b"})
        )
        respx_mock.get("https://e.com/b").mock(
            return_value=Response(302, headers={"location": "https://e.com/c"})
        )
        respx_mock.get("https://e.com/c").mock(
            return_value=Response(200, text="<html><body><h1>C</h1></body></html>")
        )
        profile = await crawler.fetch_and_parse("https://e.com/a")
        assert profile is not None
        assert profile.redirect_chain_len == 2
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_seo_crawler.py::TestPageProfileExtended -v --no-cov
```

Expected: AttributeError on the new fields.

- [ ] **Step 3: Extend `PageProfile` + parsing logic**

In `src/devrel_origin/tools/seo_crawler.py`, modify the `PageProfile` dataclass:

```python
@dataclass
class PageProfile:
    url: str
    title: str
    title_len: int
    meta_description: Optional[str]
    meta_len: int
    h1_count: int
    h_counts: dict[str, int]
    has_schema: bool
    schema_types: list[str] = field(default_factory=list)   # NEW
    internal_links_count: int = 0
    external_links_count: int = 0
    word_count: int = 0
    inp_ms: Optional[int] = None                            # NEW (PSI sets this)
    lcp_ms: Optional[int] = None                            # NEW
    redirect_chain_len: int = 0                             # NEW
    crawled_at: str = ""
```

Update `_parse_page_impl` to extract `schema_types` from JSON-LD:

```python
    # Inside _parse_page_impl, replace the `has_schema` block with:
    schema_types: list[str] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        # JSON-LD can be a single object or list; handle both
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and "@type" in item:
                t = item["@type"]
                if isinstance(t, list):
                    schema_types.extend(str(x) for x in t)
                else:
                    schema_types.append(str(t))

    # Also accept microdata itemtype attribute as a schema signal
    for el in soup.find_all(attrs={"itemtype": True}):
        itemtype = el["itemtype"]
        # itemtype is usually a URL like "https://schema.org/Product" — extract the type name
        if "/" in itemtype:
            schema_types.append(itemtype.rsplit("/", 1)[-1])

    has_schema = len(schema_types) > 0
```

(Add `import json` at the top of seo_crawler.py if not already present.)

Update `fetch_and_parse` to record the redirect chain:

```python
    async def fetch_and_parse(self, page_url: str) -> Optional[PageProfile]:
        client = await self._ensure_client()
        if not await self.is_allowed(page_url):
            logger.info(f"SEO crawler: robots.txt blocks {page_url}")
            return None
        try:
            resp = await client.get(page_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"SEO crawler: fetch failed for {page_url}: {e}")
            return None

        # `resp.history` is the list of redirect responses
        redirect_chain_len = len(resp.history)

        if self.cache_dir is not None:
            slug = (urlparse(page_url).path.replace("/", "_") or "_root")[:80]
            cache_file = self.cache_dir / f"{slug}.html"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(resp.text)

        if self.crawl_delay_ms > 0:
            await asyncio.sleep(self.crawl_delay_ms / 1000.0)

        profile = await self.parse_page(resp.text, page_url=page_url)
        # Update redirect_chain_len since parse_page can't see resp.history
        profile.redirect_chain_len = redirect_chain_len
        return profile
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_seo_crawler.py -v --no-cov
```

Expected: all PASS (existing PageParse tests + new TestPageProfileExtended).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/seo_crawler.py tests/test_seo_crawler.py
git commit -m "feat(seo): PageProfile +schema_types +inp_ms/lcp_ms +redirect_chain_len"
```

---

## Task 16: PageSpeed Insights client

**Files:**
- Create: `src/devrel_origin/tools/psi_client.py`
- Test: `tests/test_psi_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_psi_client.py`:

```python
"""PageSpeed Insights API client tests."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import respx
from httpx import Response

from devrel_origin.tools.psi_client import PsiClient, PsiResult


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "psi-cache"


@respx.mock
@pytest.mark.asyncio
async def test_query_returns_inp_lcp_in_ms(cache_dir):
    respx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed").mock(
        return_value=Response(200, json={
            "loadingExperience": {
                "metrics": {
                    "INTERACTION_TO_NEXT_PAINT": {
                        "percentile": 180,
                        "category": "FAST",
                    },
                    "LARGEST_CONTENTFUL_PAINT_MS": {
                        "percentile": 2100,
                        "category": "FAST",
                    },
                },
            },
            "lighthouseResult": {"finalUrl": "https://openclaw.ai/"},
        })
    )
    client = PsiClient(cache_dir=cache_dir)
    result = await client.query("https://openclaw.ai/")
    assert result.inp_ms == 180
    assert result.lcp_ms == 2100


@respx.mock
@pytest.mark.asyncio
async def test_cache_hit_skips_network(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Pre-populate the cache
    cache_path = cache_dir / "openclaw.ai_root.json"
    cache_path.write_text(json.dumps({
        "url": "https://openclaw.ai/",
        "inp_ms": 150,
        "lcp_ms": 1800,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))
    client = PsiClient(cache_dir=cache_dir, cache_ttl_days=30)
    # No respx mock; if the cache misses, the test fails with "no mock"
    result = await client.query("https://openclaw.ai/")
    assert result.inp_ms == 150
    assert result.lcp_ms == 1800


@respx.mock
@pytest.mark.asyncio
async def test_query_handles_missing_metrics_gracefully(cache_dir):
    """Some pages don't have CrUX data; PSI returns lighthouseResult only."""
    respx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed").mock(
        return_value=Response(200, json={
            "lighthouseResult": {"finalUrl": "https://newpage.example/"},
        })
    )
    client = PsiClient(cache_dir=cache_dir)
    result = await client.query("https://newpage.example/")
    assert result.inp_ms is None
    assert result.lcp_ms is None
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_psi_client.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the client**

Create `src/devrel_origin/tools/psi_client.py`:

```python
"""PageSpeed Insights API client.

Free tier, no auth required. Returns INP + LCP per URL. We cache results
30 days at `.devrel/seo/psi-cache/{slug}.json` to avoid re-quering on
every Selene cycle (CrUX data updates monthly anyway).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

PSI_BASE_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


@dataclass
class PsiResult:
    url: str
    inp_ms: Optional[int]
    lcp_ms: Optional[int]
    fetched_at: str  # ISO timestamp


def _slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = parsed.path.replace("/", "_") or "_root"
    return f"{host}{path}"[:100]


class PsiClient:
    """PageSpeed Insights API client with on-disk caching."""

    def __init__(self, *, cache_dir: Path, cache_ttl_days: int = 30, timeout_s: float = 30.0):
        self.cache_dir = cache_dir
        self.cache_ttl_days = cache_ttl_days
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{_slug(url)}.json"

    def _load_cache(self, url: str) -> Optional[PsiResult]:
        p = self._cache_path(url)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        fetched = datetime.fromisoformat(data["fetched_at"])
        if datetime.now(timezone.utc) - fetched > timedelta(days=self.cache_ttl_days):
            return None
        return PsiResult(
            url=data["url"],
            inp_ms=data.get("inp_ms"),
            lcp_ms=data.get("lcp_ms"),
            fetched_at=data["fetched_at"],
        )

    def _save_cache(self, result: PsiResult) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(result.url).write_text(json.dumps({
            "url": result.url,
            "inp_ms": result.inp_ms,
            "lcp_ms": result.lcp_ms,
            "fetched_at": result.fetched_at,
        }))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _fetch(self, url: str) -> PsiResult:
        resp = await self._client.get(
            PSI_BASE_URL,
            params={
                "url": url,
                "category": "performance",
                "strategy": "mobile",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        inp_ms: Optional[int] = None
        lcp_ms: Optional[int] = None
        loading = data.get("loadingExperience", {}) or {}
        metrics = loading.get("metrics", {}) or {}
        if "INTERACTION_TO_NEXT_PAINT" in metrics:
            inp_ms = int(metrics["INTERACTION_TO_NEXT_PAINT"].get("percentile") or 0) or None
        if "LARGEST_CONTENTFUL_PAINT_MS" in metrics:
            lcp_ms = int(metrics["LARGEST_CONTENTFUL_PAINT_MS"].get("percentile") or 0) or None

        return PsiResult(
            url=url,
            inp_ms=inp_ms,
            lcp_ms=lcp_ms,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    async def query(self, url: str) -> PsiResult:
        cached = self._load_cache(url)
        if cached is not None:
            return cached
        try:
            result = await self._fetch(url)
        except httpx.HTTPError as e:
            logger.warning(f"PSI fetch failed for {url}: {e}")
            result = PsiResult(
                url=url, inp_ms=None, lcp_ms=None,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        self._save_cache(result)
        return result
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_psi_client.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/psi_client.py tests/test_psi_client.py
git commit -m "feat(seo): PageSpeed Insights client with 30d on-disk cache"
```

---

## Task 17: Multi-Surface heuristic checks (extends Task 6)

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

Modifies Task 6's `_heuristic_issues_for` static method. Adds new issue kinds:
`no_typed_schema_org`, `no_typed_schema_product`, `no_llms_txt`, `inp_too_slow`,
`lcp_too_slow`, `redirect_chain_too_long`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_selene.py`:

```python
class TestMultiSurfaceHeuristics:
    def test_no_typed_schema_org_flagged(self):
        # has_schema=True but schema_types doesn't include Organization
        p = _profile(has_schema=True, schema_types=["SoftwareApplication"])
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "no_typed_schema_org" for i in issues)

    def test_organization_schema_satisfies_typed_check(self):
        p = _profile(has_schema=True, schema_types=["Organization", "WebSite"])
        issues = Selene._heuristic_issues_for(p)
        assert not any(i.kind == "no_typed_schema_org" for i in issues)

    def test_inp_too_slow_flagged(self):
        p = _profile(inp_ms=350)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "inp_too_slow" for i in issues)

    def test_inp_within_threshold_clean(self):
        p = _profile(inp_ms=180)
        issues = Selene._heuristic_issues_for(p)
        assert not any(i.kind == "inp_too_slow" for i in issues)

    def test_lcp_too_slow_flagged(self):
        p = _profile(lcp_ms=3500)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "lcp_too_slow" for i in issues)

    def test_redirect_chain_too_long_flagged(self):
        p = _profile(redirect_chain_len=4)
        issues = Selene._heuristic_issues_for(p)
        assert any(i.kind == "redirect_chain_too_long" for i in issues)

    def test_inp_lcp_none_does_not_flag(self):
        # PSI hasn't run yet — None should not produce a false positive
        p = _profile(inp_ms=None, lcp_ms=None)
        issues = Selene._heuristic_issues_for(p)
        assert not any(i.kind in ("inp_too_slow", "lcp_too_slow") for i in issues)
```

Update `_profile` helper at top of `tests/test_selene.py` to accept the new fields:

```python
def _profile(**kwargs) -> PageProfile:
    defaults = dict(
        url="https://openclaw.ai/", title="A tight 50-character title for OpenClaw",
        title_len=42, meta_description="A reasonable meta description.",
        meta_len=30, h1_count=1,
        h_counts={"h1": 1, "h2": 2, "h3": 0, "h4": 0, "h5": 0, "h6": 0},
        has_schema=True, schema_types=["Organization", "SoftwareApplication"],
        internal_links_count=5, external_links_count=2,
        word_count=400, inp_ms=180, lcp_ms=2000, redirect_chain_len=0,
        crawled_at=datetime.now(timezone.utc).isoformat(),
    )
    defaults.update(kwargs)
    return PageProfile(**defaults)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestMultiSurfaceHeuristics -v --no-cov
```

Expected: AssertionError — checks not implemented.

- [ ] **Step 3: Extend `_heuristic_issues_for`**

In `src/devrel_origin/core/selene.py`, modify the static method:

```python
    @staticmethod
    def _heuristic_issues_for(p: PageProfile) -> list[HeuristicIssue]:
        issues: list[HeuristicIssue] = []
        # ... existing checks (missing_meta, title_too_long, missing_h1,
        # duplicate_h1, no_schema, thin_content) — keep as-is ...

        # NEW Multi-Surface checks
        schema_types_lower = {s.lower() for s in (p.schema_types or [])}
        if p.has_schema and "organization" not in schema_types_lower:
            issues.append(HeuristicIssue(
                kind="no_typed_schema_org", page_url=p.url,
                detail="schema.org JSON-LD present but no `Organization` type with sameAs",
                severity="medium",
            ))
        if p.inp_ms is not None and p.inp_ms > 200:
            issues.append(HeuristicIssue(
                kind="inp_too_slow", page_url=p.url,
                detail=f"INP {p.inp_ms}ms (>200ms threshold)",
                severity="high",
            ))
        if p.lcp_ms is not None and p.lcp_ms > 2500:
            issues.append(HeuristicIssue(
                kind="lcp_too_slow", page_url=p.url,
                detail=f"LCP {p.lcp_ms}ms (>2500ms threshold)",
                severity="high",
            ))
        if p.redirect_chain_len > 3:
            issues.append(HeuristicIssue(
                kind="redirect_chain_too_long", page_url=p.url,
                detail=f"{p.redirect_chain_len} redirects before reaching this URL",
                severity="medium",
            ))

        return issues
```

The `no_llms_txt` check is per-site (not per-page); it lives on `Selene.execute()`
in Task 19 since it needs the host-level `LlmsTxt` result, not a `PageProfile`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestMultiSurfaceHeuristics tests/test_selene.py::TestHeuristics -v --no-cov
```

Expected: all PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): Multi-Surface heuristics (typed schema, INP, LCP, redirect chain)"
```

---

## Task 18: Reframed gap-analysis prompt (entity-mapping + atomic answer + information gain)

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

Replaces the `_GAP_PROMPT` constant from Task 8 with a longer prompt grounded
in the Multi-Surface framing. Output schema gains two fields:
`atomic_answer` (40-60 word draft) and `information_gain` (one-sentence
unique-insight assessment).

- [ ] **Step 1: Update the failing test from Task 8**

In `tests/test_selene.py::TestGapAnalysis::test_gap_analysis_calls_llm_with_competitor_pages`,
change the LLM mock return value to include the new fields:

```python
        llm.generate = AsyncMock(return_value=(json.dumps({
            "missing_topics": ["distributed tracing setup", "OpenTelemetry export"],
            "missing_entities": ["OpenTelemetry", "Jaeger"],
            "suggested_internal_links": ["/docs/tracing", "/docs/integrations"],
            "atomic_answer": (
                "OpenClaw is an open-source Kubernetes observability platform "
                "that auto-instruments your apps and ships unified telemetry to "
                "any OpenTelemetry-compatible backend. Install via `pipx install "
                "openclaw` and run `openclaw init` to get started."
            ),
            "information_gain": "We uniquely cover the OpenTelemetry-export path; "
                                "competitors lock you into proprietary backends.",
        }), MagicMock()))
        # ... rest of test ...
        assert "OpenTelemetry" in finding.atomic_answer
        assert finding.information_gain.startswith("We uniquely cover")
```

Add new assertion fields. Update `GapFinding` dataclass to carry them:

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestGapAnalysis -v --no-cov
```

Expected: AttributeError on `atomic_answer` field.

- [ ] **Step 3: Extend `GapFinding` + replace `_GAP_PROMPT`**

In `src/devrel_origin/core/selene.py`:

```python
@dataclass
class GapFinding:
    page_url: str
    missing_topics: list[str]
    missing_entities: list[str]
    suggested_internal_links: list[str]
    atomic_answer: str = ""           # NEW: 40-60 word top-of-page summary
    information_gain: str = ""        # NEW: one-sentence unique-insight assessment
```

Replace `_GAP_PROMPT` with:

```python
_GAP_PROMPT = """You are a Multi-Surface Search analyst. The reader will reach this
page through one of three surfaces: traditional Google, AI Overviews / SGE, or a
standalone LLM (ChatGPT/Perplexity/Claude). Your job is to identify what our page is
MISSING that lets competitors win the citation across all three surfaces.

Our page (truncated to 4KB):
URL: {our_url}
Target keyword: {target_keyword}

{our_html}

Competitor pages (each 2KB):

{competitor_blocks}

Return JSON only:

{{
  "missing_topics": ["<topic 1>", "<topic 2>"],
  "missing_entities": ["<entity>"],   // Knowledge Graph entities competitors name-drop
                                       // and we don't (e.g. specific tools, standards,
                                       // companies, technical concepts)
  "suggested_internal_links": ["/path1"],   // semantically scoped to topical cluster,
                                            // strengthens cluster authority
  "atomic_answer": "<40-60 word direct answer suitable for top-of-page extraction.>",
  "information_gain": "<one sentence: what unique insight our page offers vs.
                       top-ranking competitors? if nothing — say so.>"
}}

Rules:
- "missing_topics": specific subjects competitors cover that we don't. Don't
  list anything we already cover.
- "missing_entities": entity-first thinking — competitors name specific things
  (products, standards, companies); list what we should add.
- "atomic_answer": exactly 40-60 words. Self-contained. Extractable by an LLM
  retrieving this page for a question. State the brand value clearly.
- "information_gain": be honest. If our page is just regurgitating what
  competitors already say, return "Our page provides no unique insight versus
  competitors; consider rewriting around <specific angle>."

Return ONLY JSON, no markdown fences."""
```

Update `_analyze_gap` to return the new fields:

```python
        return GapFinding(
            page_url=our_page_url,
            missing_topics=list(data.get("missing_topics", []))[:10],
            missing_entities=list(data.get("missing_entities", []))[:10],
            suggested_internal_links=list(data.get("suggested_internal_links", []))[:5],
            atomic_answer=str(data.get("atomic_answer", ""))[:600],
            information_gain=str(data.get("information_gain", ""))[:400],
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py::TestGapAnalysis -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): reframed gap-analysis prompt with atomic answer + information gain"
```

---

## Task 19: Multi-Surface cross-pillar read of Vega's `geo_visibility`

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

Adds a `_multi_surface_aggregate` method that reads `geo_visibility` rows for
URLs in our sitemap and produces per-URL aggregates (citation_share + quality_score
across engines). Also wires `Selene.execute()` to populate `PageProfile.inp_ms`/
`lcp_ms` from the PSI client and to attach a host-level `no_llms_txt` issue when
`/llms.txt` is absent.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_selene.py`:

```python
class TestMultiSurfaceAggregate:
    def test_aggregate_pulls_geo_visibility_per_url(self, init_db, tmp_path):
        # Seed geo_visibility rows for two URLs across two engines
        with sqlite3.connect(init_db) as conn:
            conn.execute(
                "INSERT INTO geo_visibility (prompt_id, engine, period_end, "
                "is_mentioned, mention_type, position_score, citation_share, "
                "quality_score, response_path) VALUES "
                "('q1', 'perplexity', '2026-04-01', 1, 'recommended', 1, 0.6, 5, NULL),"
                "('q1', 'openai',     '2026-04-01', 1, 'direct',      2, 0.4, 4, NULL),"
                "('q2', 'perplexity', '2026-04-01', 0, NULL,           5, 0.0, 0, NULL),"
                "('q2', 'openai',     '2026-04-01', 1, 'compared',    3, 0.5, 2, NULL)"
            )
            conn.commit()

        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        agg = selene._multi_surface_aggregate(
            urls=["https://openclaw.ai/", "https://openclaw.ai/docs"],
            period_end="2026-04-01",
        )
        # The aggregate is keyed by URL but for now Vega's data is keyed by
        # prompt_id; the cross-pillar shape Selene cares about is per-URL avg
        # of citation_share + quality_score across engines for prompts that
        # mentioned this URL in their cited sources.
        # For Wave 3 with no URL→prompt linkage in geo_visibility yet, we
        # aggregate per-engine rates and surface global citation/quality
        # rather than per-URL specifics. Polish loop in Wave 4 can refine.
        assert "global" in agg
        assert agg["global"]["mentioned_share"] == pytest.approx(0.75)  # 3 of 4
        assert agg["global"]["avg_quality"] == pytest.approx((5 + 4 + 2) / 3, abs=0.01)


class TestExecuteMultiSurface:
    @pytest.mark.asyncio
    async def test_execute_attaches_no_llms_txt_issue_when_absent(self, init_db, tmp_path):
        # Crawler returns no llms.txt
        crawler = MagicMock()
        crawler.fetch_sitemap = AsyncMock(return_value=[])
        crawler.fetch_llms_txt = AsyncMock(return_value=None)
        crawler.fetch_and_parse = AsyncMock(return_value=None)
        gsc = MagicMock()
        gsc.search_analytics_query = AsyncMock(return_value=[])
        psi = MagicMock()
        psi.query = AsyncMock(return_value=None)

        selene = Selene(
            crawler=crawler, gsc_client=gsc, llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
            psi_client=psi,
        )
        report = await selene.execute(
            period_end="2026-04-01", report_id="test-report",
        )
        # When sitemap is empty AND llms.txt is absent, we still log the
        # llms.txt issue against the host root.
        assert any(i.kind == "no_llms_txt" for i in report.issues)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestMultiSurfaceAggregate tests/test_selene.py::TestExecuteMultiSurface -v --no-cov
```

Expected: AttributeError on `_multi_surface_aggregate` and `psi_client` constructor arg.

- [ ] **Step 3: Add the methods + extend `execute`**

Modify `Selene.__init__` to accept an optional `psi_client`:

```python
    def __init__(
        self,
        *,
        crawler: SEOCrawler,
        gsc_client: Any,
        llm_client: Any,
        db_path: Path,
        product_url: str,
        product_domain: str,
        gsc_property: Optional[str] = None,
        sitemap_url: Optional[str] = None,
        page_overrides: list[str] | None = None,
        competitors: list[str] | None = None,
        psi_client: Any | None = None,         # NEW
    ):
        # ... existing init ...
        self.psi = psi_client
```

Add the cross-pillar aggregator:

```python
    def _multi_surface_aggregate(
        self, *, urls: list[str], period_end: str,
    ) -> dict:
        """Read geo_visibility rows for the period and aggregate.

        Wave 3 ships a global aggregate (mention rate + avg quality across
        all engines/prompts in the period). Wave 4 polish can refine to a
        per-URL aggregate once the geo_visibility schema gets a `cited_url`
        column.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT
                    SUM(is_mentioned) * 1.0 / NULLIF(COUNT(*), 0)         AS mentioned_share,
                    AVG(citation_share) FILTER (WHERE is_mentioned = 1)   AS avg_citation,
                    AVG(quality_score)  FILTER (WHERE is_mentioned = 1)   AS avg_quality
                FROM geo_visibility
                WHERE period_end = ?
                """,
                (period_end,),
            )
            row = cur.fetchone()
        return {
            "global": {
                "mentioned_share": row[0] or 0.0,
                "avg_citation_share": row[1] or 0.0,
                "avg_quality": row[2] or 0.0,
            },
        }
```

Note: SQLite ≥ 3.30 supports `FILTER (WHERE ...)`. macOS bundled Python's
`sqlite3` is 3.39+, so this works on dev. CI uses Linux Python 3.12/3.13
which bundles 3.40+. Confirmed compatible.

Modify `Selene.execute()` to:
1. Call `psi_client.query` for each crawled URL (concurrently via `asyncio.gather`)
   and update `PageProfile.inp_ms`/`lcp_ms` before heuristics run.
2. Call `crawler.fetch_llms_txt()` once at host root and append a
   `no_llms_txt` issue when absent.
3. Call `_multi_surface_aggregate` and stash on the report.

```python
    # Add to SeoReport dataclass:
    multi_surface: dict = field(default_factory=dict)   # NEW


    async def execute(
        self, *,
        period_end: str, report_id: str,
        rex_competitive_html: dict[str, dict[str, str]] | None = None,
        deliverables_dir: Path | None = None,
    ) -> SeoReport:
        rex_competitive_html = rex_competitive_html or {}

        # ... existing sitemap fetch + crawl ...

        # NEW: llms.txt presence check
        host_root = self.product_url.rstrip("/")
        llms = await self.crawler.fetch_llms_txt(f"{host_root}/llms.txt")
        if llms is None or not getattr(llms, "is_present", False):
            issues.insert(0, HeuristicIssue(
                kind="no_llms_txt", page_url=host_root,
                detail="No `/llms.txt` published; AI crawlers have no curated map of recommended pages.",
                severity="medium",
            ))

        # NEW: Populate INP/LCP via PSI for top pages (cap at 20 to respect free tier)
        if self.psi is not None and profiles:
            top_for_psi = profiles[:20]
            psi_tasks = [self.psi.query(p.url) for p in top_for_psi]
            psi_results = await asyncio.gather(*psi_tasks, return_exceptions=True)
            for prof, psi_res in zip(top_for_psi, psi_results, strict=True):
                if isinstance(psi_res, Exception) or psi_res is None:
                    continue
                prof.inp_ms = psi_res.inp_ms
                prof.lcp_ms = psi_res.lcp_ms

        # ... existing heuristic + GSC + gap analysis ...

        # NEW: Multi-Surface aggregate (cross-pillar from Vega)
        multi_surface = self._multi_surface_aggregate(
            urls=[p.url for p in profiles], period_end=period_end,
        )

        report = SeoReport(
            period_end=period_end, profiles=profiles, issues=issues,
            keyword_opportunities=keyword_opportunities, gap_findings=gap_findings,
            multi_surface=multi_surface,
        )
        # ... existing persist + brief ...
        return report
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_selene.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): Multi-Surface aggregate (cross-pillar geo_visibility) + PSI in execute"
```

---

## Task 20: Persist + brief integration for Multi-Surface fields

**Files:**
- Modify: `src/devrel_origin/core/selene.py`
- Modify: `tests/test_selene.py`

Touches Task 9's `_persist_page_profiles` to write the new columns
(`schema_types_json`, `inp_ms`, `lcp_ms`, `redirect_chain_len`) and Task 10's
`_write_briefs` to surface Multi-Surface findings (atomic answer suggestion,
information gain, citation status from `multi_surface` aggregate).

- [ ] **Step 1: Update the persist test**

In `tests/test_selene.py::TestPersist::test_persist_writes_page_profiles`,
modify the assertion to check the new columns:

```python
    def test_persist_writes_page_profiles(self, init_db, tmp_path):
        selene = Selene(
            crawler=MagicMock(), gsc_client=MagicMock(), llm_client=MagicMock(),
            db_path=init_db,
            product_url="https://openclaw.ai/", product_domain="openclaw.ai",
        )
        profiles = [_profile(inp_ms=180, lcp_ms=2100, redirect_chain_len=1,
                              schema_types=["Organization", "SoftwareApplication"])]
        selene._persist_page_profiles(profiles, period_end="2026-04-01")
        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT page_url, schema_types_json, inp_ms, lcp_ms, redirect_chain_len "
                "FROM seo_page_profiles"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "https://openclaw.ai/"
        types = json.loads(rows[0][1])
        assert "Organization" in types
        assert rows[0][2] == 180
        assert rows[0][3] == 2100
        assert rows[0][4] == 1
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_selene.py::TestPersist::test_persist_writes_page_profiles -v --no-cov
```

Expected: failure on schema_types_json column missing OR sqlite error on extra columns.

- [ ] **Step 3: Update `_persist_page_profiles`**

In `core/selene.py`:

```python
    def _persist_page_profiles(
        self, profiles: list[PageProfile], *, period_end: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            for p in profiles:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO seo_page_profiles
                        (page_url, period_end, title_len, meta_len, h1_count,
                         word_count, has_schema, schema_types_json,
                         internal_links, inp_ms, lcp_ms, redirect_chain_len,
                         crawled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.url, period_end,
                        p.title_len, p.meta_len, p.h1_count,
                        p.word_count, 1 if p.has_schema else 0,
                        json.dumps(p.schema_types or []),
                        p.internal_links_count,
                        p.inp_ms, p.lcp_ms, p.redirect_chain_len,
                        p.crawled_at,
                    ),
                )
            conn.commit()
```

Update `_write_briefs` to surface the Multi-Surface bits in the brief markdown.
For each `rewrite × url` recommendation that has a corresponding `GapFinding`,
include the atomic_answer + information_gain in the brief:

```python
    def _write_briefs(self, report: SeoReport, deliverables_dir: Path) -> None:
        deliverables_dir.mkdir(parents=True, exist_ok=True)
        gap_by_url = {g.page_url: g for g in report.gap_findings}

        for rec in report.recommendations:
            md_lines = [
                f"# Selene brief: {rec.action} `{rec.target}`",
                "",
                f"**Period:** {report.period_end}",
                f"**Pillar:** seo",
                f"**Target kind:** {rec.target_kind.value}",
                f"**Confidence:** {rec.confidence:.2f}",
                "",
            ]
            if rec.source_ids:
                md_lines.extend(["## Why", ""])
                md_lines.extend(f"- {sid}" for sid in rec.source_ids)
                md_lines.append("")

            # NEW: Multi-Surface section for rewrite × url with gap data
            gap = gap_by_url.get(rec.target)
            if rec.action == "rewrite" and gap is not None:
                if gap.atomic_answer:
                    md_lines.extend([
                        "## Suggested atomic answer (40-60 words)",
                        "",
                        f"> {gap.atomic_answer}",
                        "",
                    ])
                if gap.information_gain:
                    md_lines.extend([
                        "## Information gain assessment",
                        "",
                        f"{gap.information_gain}",
                        "",
                    ])
                if gap.missing_entities:
                    md_lines.extend([
                        "## Missing entities (Knowledge Graph)",
                        "",
                    ])
                    md_lines.extend(f"- {e}" for e in gap.missing_entities)
                    md_lines.append("")

            # NEW: Multi-Surface global stats from geo_visibility
            if report.multi_surface:
                ms = report.multi_surface.get("global", {})
                md_lines.extend([
                    "## Multi-Surface visibility (this period)",
                    "",
                    f"- Mentioned in AI search: {ms.get('mentioned_share', 0):.1%}",
                    f"- Avg citation share: {ms.get('avg_citation_share', 0):.1%}",
                    f"- Avg answer quality: {ms.get('avg_quality', 0):.1f}/5",
                    "",
                ])

            md_lines.extend(["## Suggested next steps", ""])
            if rec.action == "rewrite":
                md_lines.append(f"- Kai: rewrite `{rec.target}` to address the items above. Lead with the atomic answer block.")
            elif rec.action == "amplify":
                md_lines.append(f"- Mox: produce a piece of content targeting the keyword `{rec.target}`.")
            elif rec.action == "investigate":
                md_lines.append(f"- Manual: review `{rec.target}` and address the flagged technical issue.")

            slug = rec.target.replace("https://", "").replace("/", "-").replace(" ", "-")[:60]
            path = deliverables_dir / f"seo-brief-{report.period_end}-{rec.action}-{slug}.md"
            path.write_text("\n".join(md_lines) + "\n")
```

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_selene.py -v --no-cov
pytest tests/ -q --no-header
ruff check . && ruff format --check . | tail -1
```

Expected: all PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/selene.py tests/test_selene.py
git commit -m "feat(selene): persist Multi-Surface profile fields + brief integration"
```

---

## Wave 3 closeout checklist

- [ ] `pytest tests/ -q --no-header` shows ~890 + ~50 new = ~940 passed / 21 xfailed
- [ ] `ruff check .` and `ruff format --check .` both clean
- [ ] `devrel seo --help` lists `connect-gsc`, `crawl`, `report`, `history`, `diff`, `calibration`
- [ ] After running `devrel seo connect-gsc` (manual), `~/.devrel/credentials/gsc.json` exists with mode 0600
- [ ] `devrel seo crawl` walks the sitemap and prints page profiles with `schema_types`, INP, LCP, `redirect_chain_len` columns populated (manual smoke against a real site)
- [ ] `devrel seo report` runs end-to-end including PSI calls + llms.txt check + Multi-Surface aggregate (manual smoke; budget ~$0.40 + free PSI)
- [ ] At least one `seo-brief-*.md` lands in `.devrel/deliverables/` and contains the atomic-answer block + information-gain assessment + Multi-Surface visibility section
- [ ] `devrel growth summary` shows non-zero "Open recs" for the seo pillar
- [ ] Atlas weekly cycle with `seo_in_run = true` runs Selene without breaking other agents
- [ ] (External) Google OAuth verification status checked — should be in review or approved by now

When all checked: Wave 3 complete. Move to Wave 4 plan (`growth-wave4-polish-release.md`).
