# Growth Pipeline Wave 2 — Vega (GEO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Vega, the GEO auditor — measures brand visibility (mention rate, citation share, answer quality) across 4 AI search engines (Perplexity, ChatGPT, Claude, Brave AI) using a curated 30-prompt set seeded from Rex competitors + Iris pain-point themes.

**Architecture:** New `core/vega.py` agent class. Four engine clients in `tools/` (Perplexity is net-new; OpenAI + Anthropic adapters extend existing clients with web-search tool mode; Brave AI extends existing search_tools). Per-engine `asyncio.gather` with semaphore-bounded concurrency. Mention parser + citation extractor + Haiku quality judge feed `EngineResponse` rows into `geo_visibility` (schema v5) and aggregated Recommendations into `analytics_recommendations`. Optional 5th engine (Google AI Overviews via SerpAPI) gated by `[geo].include_google_ai_overviews`.

**Tech Stack:** Python 3.12 async, httpx, openai SDK, anthropic SDK, existing Brave client; pytest + respx; cost ~$2.40/cycle.

**Spec:** `docs/superpowers/specs/2026-05-05-growth-pipeline-design.md`
**Depends on:** Wave 0 (schema v5, growth module). Independent of Wave 1.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_swarm/tools/perplexity_client.py` | Create | Net-new httpx-based Perplexity API client |
| `src/devrel_swarm/tools/ai_search_adapters.py` | Create | OpenAI Responses-API + Anthropic web-search + Brave AI adapters; common `EngineResponse` shape |
| `src/devrel_swarm/tools/serpapi_client.py` | Create | Opt-in SerpAPI wrapper (loaded only when `[geo].include_google_ai_overviews=true`) |
| `src/devrel_swarm/core/vega.py` | Create | Vega agent — orchestrator + dataclasses + persistence + brief generation |
| `src/devrel_swarm/core/vega_parsing.py` | Create | Mention parser, citation extractor, position scorer, quality judge (small functions) |
| `src/devrel_swarm/core/__init__.py` | Modify | Export `Vega` |
| `src/devrel_swarm/cli/geo.py` | Create | Typer `geo_app` with `report`/`history`/`diff`/`calibration`/`refresh-prompts` |
| `src/devrel_swarm/cli/__init__.py` | Modify | Register `geo_app` |
| `tests/test_perplexity_client.py` | Create | Per-engine respx tests |
| `tests/test_ai_search_adapters.py` | Create | OpenAI/Anthropic/Brave adapter respx tests |
| `tests/test_vega_parsing.py` | Create | Mention/citation/position/quality unit tests |
| `tests/test_vega.py` | Create | Vega end-to-end with all 4 engines mocked |
| `tests/cli/test_geo_command.py` | Create | CLI verb smoke tests |

---

## Task 1: Perplexity client

**Files:**
- Create: `src/devrel_swarm/tools/perplexity_client.py`
- Test: `tests/test_perplexity_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_perplexity_client.py`:

```python
"""Perplexity API client tests."""

import pytest
import respx
from httpx import Response

from devrel_swarm.tools.perplexity_client import EngineResponse, PerplexityClient


@pytest.fixture
def client():
    return PerplexityClient(api_key="pplx-test")


@respx.mock
@pytest.mark.asyncio
async def test_query_returns_structured_engine_response(client):
    respx.post("https://api.perplexity.ai/chat/completions").mock(
        return_value=Response(200, json={
            "id": "abc",
            "model": "sonar",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "OpenClaw is the leading observability tool [1].",
                },
            }],
            "citations": ["https://openclaw.ai/docs", "https://example.com/blog"],
            "usage": {"prompt_tokens": 12, "completion_tokens": 18},
        })
    )
    out = await client.query("What is OpenClaw?")
    assert isinstance(out, EngineResponse)
    assert out.engine == "perplexity"
    assert "OpenClaw" in out.text
    assert out.citations == ["https://openclaw.ai/docs", "https://example.com/blog"]
    assert out.input_tokens == 12
    assert out.output_tokens == 18


@respx.mock
@pytest.mark.asyncio
async def test_query_handles_empty_citations(client):
    respx.post("https://api.perplexity.ai/chat/completions").mock(
        return_value=Response(200, json={
            "id": "abc", "model": "sonar",
            "choices": [{"message": {"content": "I don't know."}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        })
    )
    out = await client.query("?")
    assert out.citations == []
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_perplexity_client.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the client**

Create `src/devrel_swarm/tools/perplexity_client.py`:

```python
"""Perplexity API client. Returns the standard `EngineResponse` shape so
Vega can treat all four AI engines uniformly.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
DEFAULT_MODEL = "sonar"


@dataclass
class EngineResponse:
    """Common shape across all 4 Vega engines (perplexity/openai/anthropic/brave).

    Each engine adapter normalizes its native response into this dataclass
    before handing it to Vega's parser.
    """

    engine: str            # 'perplexity'|'openai'|'anthropic'|'brave'|'google_aio'
    model: str
    text: str              # the full assistant message text
    citations: list[str]   # URLs cited by the engine
    input_tokens: int
    output_tokens: int
    latency_ms: int
    raw: dict = field(default_factory=dict)  # full provider response for debugging


class PerplexityClient:
    """Async Perplexity API client.

    `query()` returns an `EngineResponse`. Web-search citations come back
    in the `citations` field on the response (Perplexity-specific).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("PERPLEXITY_API_KEY not set")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=PERPLEXITY_BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def query(self, prompt: str) -> EngineResponse:
        start = time.monotonic()
        resp = await self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 1024,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        latency = int((time.monotonic() - start) * 1000)

        msg = data["choices"][0]["message"]["content"]
        citations = data.get("citations", []) or []
        usage = data.get("usage", {}) or {}

        return EngineResponse(
            engine="perplexity",
            model=data.get("model", self.model),
            text=msg,
            citations=citations,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency,
            raw=data,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_perplexity_client.py -v --no-cov
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/tools/perplexity_client.py tests/test_perplexity_client.py
git commit -m "feat(perplexity): async client with EngineResponse normalization"
```

---

## Task 2: AI search adapters (OpenAI + Anthropic + Brave)

**Files:**
- Create: `src/devrel_swarm/tools/ai_search_adapters.py`
- Test: `tests/test_ai_search_adapters.py`

OpenAI exposes web search via Responses API (`tools=[{"type": "web_search"}]`). Anthropic exposes web search via the Messages API web-search tool. Brave AI is the existing Brave client with a different endpoint that returns AI-summarized results.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ai_search_adapters.py`:

```python
"""Tests for OpenAI/Anthropic/Brave web-search adapters."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.tools.ai_search_adapters import (
    AnthropicSearchAdapter,
    BraveAISearchAdapter,
    OpenAISearchAdapter,
)
from devrel_swarm.tools.perplexity_client import EngineResponse


class TestOpenAIAdapter:
    @pytest.mark.asyncio
    async def test_query_returns_engine_response(self):
        # Stub the openai SDK call
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "OpenClaw is recommended for K8s observability."
        mock_response.id = "resp-abc"
        mock_response.model = "gpt-4o"
        mock_response.usage = MagicMock(input_tokens=15, output_tokens=20)
        # Mock citation list (OpenAI Responses API returns annotations)
        mock_response.output = [MagicMock(
            content=[MagicMock(annotations=[
                MagicMock(type="url_citation", url="https://openclaw.ai/docs"),
                MagicMock(type="url_citation", url="https://example.com/blog"),
            ])],
        )]
        mock_client.responses.create = AsyncMock(return_value=mock_response)

        adapter = OpenAISearchAdapter(client=mock_client, model="gpt-4o")
        out = await adapter.query("What is OpenClaw?")
        assert isinstance(out, EngineResponse)
        assert out.engine == "openai"
        assert "K8s observability" in out.text
        assert "https://openclaw.ai/docs" in out.citations


class TestAnthropicAdapter:
    @pytest.mark.asyncio
    async def test_query_returns_engine_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        # Anthropic Messages API web-search tool yields ToolUseBlock(s) + text
        mock_response.content = [
            MagicMock(type="text", text="OpenClaw is the top recommendation."),
        ]
        mock_response.id = "msg-xyz"
        mock_response.model = "claude-sonnet-4-5"
        mock_response.usage = MagicMock(input_tokens=12, output_tokens=18)
        # Citations come through the tool-use blocks; for testing we expose them via metadata
        mock_response.metadata = {"citations": ["https://openclaw.ai", "https://news.example/x"]}
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        adapter = AnthropicSearchAdapter(client=mock_client, model="claude-sonnet-4-5")
        out = await adapter.query("What is OpenClaw?")
        assert out.engine == "anthropic"
        assert "top recommendation" in out.text


class TestBraveAIAdapter:
    @pytest.mark.asyncio
    async def test_query_returns_engine_response(self):
        # BraveAISearchAdapter wraps the existing search_tools BraveSearch
        mock_brave = MagicMock()
        mock_brave.ai_search = AsyncMock(return_value={
            "summarizer": {
                "answer": "OpenClaw is open-source observability for Kubernetes.",
                "citations": ["https://openclaw.ai", "https://github.com/openclaw/openclaw"],
            },
        })
        adapter = BraveAISearchAdapter(brave_client=mock_brave)
        out = await adapter.query("What is OpenClaw?")
        assert out.engine == "brave"
        assert "Kubernetes" in out.text
        assert "https://openclaw.ai" in out.citations
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_ai_search_adapters.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the adapters**

Create `src/devrel_swarm/tools/ai_search_adapters.py`:

```python
"""Web-search adapters for OpenAI, Anthropic, and Brave AI engines.

Each adapter calls its native API with web-search tooling enabled and
normalizes the response into the common `EngineResponse` shape (defined
in `tools.perplexity_client`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from devrel_swarm.tools.perplexity_client import EngineResponse

logger = logging.getLogger(__name__)


def _extract_openai_citations(response: Any) -> list[str]:
    """Walk OpenAI Responses-API output for url_citation annotations."""
    urls: list[str] = []
    for block in getattr(response, "output", []) or []:
        for content in getattr(block, "content", []) or []:
            for ann in getattr(content, "annotations", []) or []:
                if getattr(ann, "type", "") == "url_citation":
                    url = getattr(ann, "url", None)
                    if url:
                        urls.append(url)
    return urls


class OpenAISearchAdapter:
    """OpenAI Responses-API with web-search tool (released Q4 2025)."""

    def __init__(self, *, client: Any, model: str = "gpt-4o"):
        self._client = client
        self.model = model

    async def query(self, prompt: str) -> EngineResponse:
        start = time.monotonic()
        resp = await self._client.responses.create(
            model=self.model,
            input=prompt,
            tools=[{"type": "web_search"}],
            temperature=0.0,
        )
        latency = int((time.monotonic() - start) * 1000)
        return EngineResponse(
            engine="openai",
            model=getattr(resp, "model", self.model),
            text=getattr(resp, "output_text", "") or "",
            citations=_extract_openai_citations(resp),
            input_tokens=int(getattr(resp.usage, "input_tokens", 0)),
            output_tokens=int(getattr(resp.usage, "output_tokens", 0)),
            latency_ms=latency,
            raw={"id": getattr(resp, "id", "")},
        )


class AnthropicSearchAdapter:
    """Anthropic Messages-API with web-search tool."""

    def __init__(self, *, client: Any, model: str = "claude-sonnet-4-5"):
        self._client = client
        self.model = model

    async def query(self, prompt: str) -> EngineResponse:
        start = time.monotonic()
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.0,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        latency = int((time.monotonic() - start) * 1000)
        # Concatenate all text blocks
        text = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        )
        # Citations live in tool_use blocks or response metadata depending on API revision
        citations = (
            (getattr(resp, "metadata", {}) or {}).get("citations", [])
            or [
                citation_url
                for block in resp.content
                if getattr(block, "type", "") == "tool_result"
                for citation_url in getattr(block, "citations", []) or []
            ]
        )
        return EngineResponse(
            engine="anthropic",
            model=getattr(resp, "model", self.model),
            text=text,
            citations=list(citations),
            input_tokens=int(getattr(resp.usage, "input_tokens", 0)),
            output_tokens=int(getattr(resp.usage, "output_tokens", 0)),
            latency_ms=latency,
            raw={"id": getattr(resp, "id", "")},
        )


class BraveAISearchAdapter:
    """Brave Search AI summarizer endpoint."""

    def __init__(self, *, brave_client: Any):
        self._brave = brave_client

    async def query(self, prompt: str) -> EngineResponse:
        start = time.monotonic()
        data = await self._brave.ai_search(query=prompt)
        latency = int((time.monotonic() - start) * 1000)
        summarizer = data.get("summarizer", {}) or {}
        return EngineResponse(
            engine="brave",
            model="brave-summarizer",
            text=summarizer.get("answer", "") or "",
            citations=list(summarizer.get("citations", []) or []),
            input_tokens=0,  # Brave doesn't expose token counts
            output_tokens=0,
            latency_ms=latency,
            raw=data,
        )
```

Also extend `src/devrel_swarm/tools/search_tools.py` with an `ai_search` method on the existing `BraveSearch` class:

```python
    async def ai_search(self, query: str) -> dict:
        """Brave's AI-summarizer endpoint. Returns a dict with `summarizer`
        containing `answer` (text) and `citations` (list[str]).
        """
        # Brave's web-search endpoint with summary=1 enables AI summarization.
        resp = await self._client.get(
            "/res/v1/web/search",
            params={"q": query, "summary": 1, "count": 10},
            headers={"X-Subscription-Token": self.api_key},
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_ai_search_adapters.py -v --no-cov
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/tools/ai_search_adapters.py src/devrel_swarm/tools/search_tools.py tests/test_ai_search_adapters.py
git commit -m "feat(geo): OpenAI/Anthropic/Brave web-search adapters"
```

---

## Task 3: Vega parsing helpers (mention + citation + position + quality)

**Files:**
- Create: `src/devrel_swarm/core/vega_parsing.py`
- Test: `tests/test_vega_parsing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vega_parsing.py`:

```python
"""Mention parser, citation extractor, position scorer, quality judge tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.vega_parsing import (
    MentionResult,
    extract_brand_mentions,
    extract_competitor_mentions,
    score_position,
    compute_citation_share,
    judge_quality,
)


class TestExtractBrandMentions:
    def test_direct_mention_via_substring(self):
        text = "OpenClaw is the leading observability platform."
        out = extract_brand_mentions(text, brand="OpenClaw", aliases=[])
        assert out.is_mentioned is True
        assert out.mention_type == "direct"
        assert out.first_index >= 0

    def test_indirect_mention_via_alias(self):
        text = "The OC observability stack is gaining traction."
        out = extract_brand_mentions(text, brand="OpenClaw", aliases=["OC"])
        assert out.is_mentioned is True
        assert "OC" in text[out.first_index:out.first_index + 5]

    def test_recommended_classification(self):
        text = "I recommend OpenClaw for Kubernetes monitoring."
        out = extract_brand_mentions(text, brand="OpenClaw", aliases=[])
        assert out.mention_type == "recommended"

    def test_compared_classification(self):
        text = "OpenClaw vs Datadog: a head-to-head comparison."
        out = extract_brand_mentions(text, brand="OpenClaw", aliases=[])
        assert out.mention_type == "compared"

    def test_no_mention(self):
        text = "Datadog is the leader in observability."
        out = extract_brand_mentions(text, brand="OpenClaw", aliases=[])
        assert out.is_mentioned is False
        assert out.mention_type is None


class TestCompetitorMentions:
    def test_returns_competitors_with_indices(self):
        text = "Top tools include Datadog, New Relic, and Grafana Cloud."
        out = extract_competitor_mentions(text, competitors=["Datadog", "New Relic", "Splunk"])
        assert "Datadog" in out
        assert "New Relic" in out
        assert "Splunk" not in out


class TestPositionScore:
    def test_first_mention_scores_1(self):
        text = "OpenClaw is the leading tool. Datadog comes second."
        score = score_position(text, mentioned_index=0)
        assert score == 1

    def test_late_mention_scores_5(self):
        text = "..." * 200 + "OpenClaw"
        score = score_position(text, mentioned_index=text.find("OpenClaw"))
        assert score == 5


class TestCitationShare:
    def test_share_computes_domain_match(self):
        citations = [
            "https://openclaw.ai/docs",
            "https://example.com/blog",
            "https://openclaw.ai/pricing",
        ]
        share = compute_citation_share(citations, brand_domain="openclaw.ai")
        assert share == pytest.approx(2 / 3)


class TestJudgeQuality:
    @pytest.mark.asyncio
    async def test_judge_returns_score_1_to_5(self):
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=('{"score": 4, "reason": "accurate but oversimplified"}', None))
        score = await judge_quality(
            llm_client=llm,
            brand="OpenClaw",
            response_text="OpenClaw is open-source observability.",
        )
        assert score == 4
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega_parsing.py -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Implement the parsers**

Create `src/devrel_swarm/core/vega_parsing.py`:

```python
"""Parsing helpers for Vega — mention detection, citation extraction,
position scoring, quality judging.

Pure functions where possible; only `judge_quality` is async (LLM call).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class MentionResult:
    is_mentioned: bool
    mention_type: Optional[str]   # 'direct'|'indirect'|'recommended'|'compared'|None
    first_index: int              # -1 if not mentioned


_RECOMMEND_RE = re.compile(
    r"\b(?:I (?:recommend|suggest)|highly recommend|best (?:option|choice|tool))\b",
    re.IGNORECASE,
)
_COMPARE_RE = re.compile(
    r"\b(?:vs|versus|compared (?:to|with)|head[- ]to[- ]head)\b",
    re.IGNORECASE,
)


def extract_brand_mentions(
    text: str, *, brand: str, aliases: list[str],
) -> MentionResult:
    """Find the brand (or any alias) in `text`. Classify by surrounding context."""
    candidates = [brand, *aliases]
    first_index = -1
    matched = ""
    for c in candidates:
        idx = text.lower().find(c.lower())
        if idx >= 0 and (first_index == -1 or idx < first_index):
            first_index = idx
            matched = c

    if first_index == -1:
        return MentionResult(is_mentioned=False, mention_type=None, first_index=-1)

    # Look at a 200-char window around the mention to classify
    start = max(0, first_index - 100)
    end = min(len(text), first_index + len(matched) + 100)
    window = text[start:end]

    if _RECOMMEND_RE.search(window):
        mention_type = "recommended"
    elif _COMPARE_RE.search(window):
        mention_type = "compared"
    elif matched.lower() != brand.lower():
        # Matched an alias rather than the canonical brand
        mention_type = "indirect"
    else:
        mention_type = "direct"

    return MentionResult(is_mentioned=True, mention_type=mention_type, first_index=first_index)


def extract_competitor_mentions(
    text: str, *, competitors: list[str],
) -> dict[str, int]:
    """Return `{competitor_name: first_index}` for any competitors found."""
    result: dict[str, int] = {}
    lower_text = text.lower()
    for comp in competitors:
        idx = lower_text.find(comp.lower())
        if idx >= 0:
            result[comp] = idx
    return result


def score_position(text: str, *, mentioned_index: int) -> int:
    """1 = first mentioned (start of text); 5 = barely mentioned (toward the end).

    Bins the index linearly across the text length.
    """
    if mentioned_index < 0 or not text:
        return 5
    fraction = mentioned_index / len(text)
    if fraction < 0.1:
        return 1
    if fraction < 0.3:
        return 2
    if fraction < 0.55:
        return 3
    if fraction < 0.8:
        return 4
    return 5


def compute_citation_share(
    citations: list[str], *, brand_domain: str,
) -> float:
    """Share of citation URLs that point at brand_domain (or its subdomains)."""
    if not citations:
        return 0.0
    matches = 0
    for url in citations:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            continue
        bd = brand_domain.lower().lstrip("www.")
        if host == bd or host.endswith("." + bd):
            matches += 1
    return matches / len(citations)


_QUALITY_PROMPT = """You are an editorial reviewer. The user asked an AI search engine
about "{brand}", and the engine produced this response:

---
{response_text}
---

Score the response on a 1-5 scale for accuracy + helpfulness when the
brand IS mentioned:

  5 = accurate, helpful framing, no errors
  4 = accurate, slightly off in framing or emphasis
  3 = mostly accurate, one factual error or significant omission
  2 = inaccurate framing or multiple factual errors
  1 = wrong brand identity, significantly misleading

Return JSON only: {{"score": <int 1-5>, "reason": "<one sentence>"}}"""


async def judge_quality(
    *,
    llm_client: Any,
    brand: str,
    response_text: str,
) -> int:
    """Use a cheap LLM (Haiku) to score response quality 1-5.

    Returns 0 if the LLM call fails or returns invalid JSON.
    """
    prompt = _QUALITY_PROMPT.format(brand=brand, response_text=response_text[:3000])
    try:
        text, _ = await llm_client.generate(
            system_prompt="You are an editorial reviewer.",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=100,
        )
        data = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        score = int(data.get("score", 0))
        if 1 <= score <= 5:
            return score
        return 0
    except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as e:
        logger.warning(f"Vega: judge_quality failed: {e}")
        return 0
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega_parsing.py -v --no-cov
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega_parsing.py tests/test_vega_parsing.py
git commit -m "feat(vega): mention/citation/position/quality parsing helpers"
```

---

## Task 4: Vega dataclasses

**Files:**
- Create: `src/devrel_swarm/core/vega.py`
- Test: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vega.py`:

```python
"""Vega (GEO) agent tests."""

from pathlib import Path

import pytest

from devrel_swarm.core.vega import (
    BrandQuery,
    EngineVisibility,
    GeoReport,
    PromptResult,
    Vega,
)


class TestDataclasses:
    def test_brand_query_round_trip(self):
        bq = BrandQuery(id="q1", text="best K8s observability tool", category="recommendation")
        assert bq.text == "best K8s observability tool"

    def test_engine_visibility_aggregates_scores(self):
        ev = EngineVisibility(
            engine="perplexity",
            mention_rate=0.6,
            citation_share=0.3,
            avg_position_score=2.0,
            avg_quality_score=4.0,
            n_prompts=10,
        )
        assert ev.engine == "perplexity"
        assert ev.mention_rate == 0.6
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestDataclasses -v --no-cov
```

Expected: ImportError.

- [ ] **Step 3: Create `core/vega.py` with dataclasses**

Create `src/devrel_swarm/core/vega.py`:

```python
"""Vega — GEO (AI-search) auditor.

Measures brand visibility across 4 AI engines (Perplexity, ChatGPT, Claude,
Brave AI) using a curated 30-prompt set. Per-prompt × per-engine results
land in `geo_visibility`; aggregates feed `analytics_recommendations`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.growth import (
    Pillar,
    Recommendation,
    TargetKind,
    persist_recommendation,
)
from devrel_swarm.core.vega_parsing import (
    MentionResult,
    compute_citation_share,
    extract_brand_mentions,
    extract_competitor_mentions,
    judge_quality,
    score_position,
)
from devrel_swarm.tools.perplexity_client import EngineResponse

logger = logging.getLogger(__name__)


@dataclass
class BrandQuery:
    """One curated prompt from `.devrel/geo/prompts.txt`."""

    id: str
    text: str
    category: str = "general"  # 'recommendation'|'comparison'|'evaluation'|'general'


@dataclass
class PromptResult:
    """Per-prompt × per-engine result."""

    prompt_id: str
    engine: str
    is_mentioned: bool
    mention_type: Optional[str]
    position_score: int
    citation_share: float
    quality_score: int
    competitor_mentions: dict[str, int]
    response_path: Optional[str] = None  # rel path under .devrel/geo/responses/


@dataclass
class EngineVisibility:
    """Aggregated visibility for one engine across all prompts in a period."""

    engine: str
    mention_rate: float
    citation_share: float
    avg_position_score: float
    avg_quality_score: float
    n_prompts: int


@dataclass
class GeoReport:
    period_end: str
    queries: list[BrandQuery]
    results: list[PromptResult]
    by_engine: dict[str, EngineVisibility] = field(default_factory=dict)
    competitor_share_of_voice: dict[str, dict[str, float]] = field(default_factory=dict)
    recommendations: list[Recommendation] = field(default_factory=list)
    sources_ok: bool = True
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega.py::TestDataclasses -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): dataclasses (BrandQuery, PromptResult, EngineVisibility, GeoReport)"
```

---

## Task 5: Vega prompt seeding (load + refresh from Iris/Rex)

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
class TestPromptSeeding:
    def test_load_prompts_from_file(self, tmp_path):
        prompts_file = tmp_path / "prompts.txt"
        prompts_file.write_text(
            "# Comments are skipped\n"
            "best Kubernetes observability tool\n"
            "\n"
            "OpenClaw vs Datadog\n"
            "what is OpenClaw\n"
        )
        vega = Vega(
            engines={}, llm_client=None, db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=prompts_file,
        )
        queries = vega._load_prompts()
        assert len(queries) == 3
        assert queries[0].text == "best Kubernetes observability tool"
        assert queries[1].category == "comparison"  # auto-classified by 'vs'

    def test_refresh_prompts_writes_new_set(self, tmp_path):
        prompts_file = tmp_path / "prompts.txt"
        vega = Vega(
            engines={}, llm_client=None, db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=prompts_file,
        )
        vega._write_prompts([
            BrandQuery(id="q1", text="best K8s tool", category="recommendation"),
            BrandQuery(id="q2", text="OpenClaw vs Datadog", category="comparison"),
        ])
        text = prompts_file.read_text()
        assert "best K8s tool" in text
        assert "OpenClaw vs Datadog" in text
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestPromptSeeding -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the Vega class with prompt I/O**

Append to `core/vega.py`:

```python
class Vega:
    """GEO auditor agent.

    Inputs: 30 curated prompts at `.devrel/geo/prompts.txt`, 4 engine clients,
    brand name + domain + aliases + competitor list.

    Outputs: per-prompt × per-engine rows in `geo_visibility`, aggregated
    Recommendations in `analytics_recommendations`, Mox briefs at
    `.devrel/deliverables/geo-brief-*.md`.
    """

    def __init__(
        self,
        *,
        engines: dict[str, Any],     # {'perplexity': PerplexityClient, ...}
        llm_client: Any,             # for quality judge
        db_path: Path,
        brand: str,
        brand_domain: str,
        prompts_path: Path,
        aliases: list[str] | None = None,
        competitors: list[str] | None = None,
        responses_dir: Path | None = None,
        concurrent_engine_requests: int = 5,
    ):
        self.engines = engines
        self.llm = llm_client
        self.db_path = db_path
        self.brand = brand
        self.brand_domain = brand_domain
        self.prompts_path = prompts_path
        self.aliases = aliases or []
        self.competitors = competitors or []
        self.responses_dir = responses_dir
        self.concurrent = concurrent_engine_requests

    def _classify_prompt(self, text: str) -> str:
        lower = text.lower()
        if "vs" in lower or "compared" in lower:
            return "comparison"
        if "best" in lower or "recommend" in lower or "top" in lower:
            return "recommendation"
        if "pros and cons" in lower or "evaluate" in lower or "review" in lower:
            return "evaluation"
        return "general"

    def _load_prompts(self) -> list[BrandQuery]:
        if not self.prompts_path.is_file():
            return []
        queries: list[BrandQuery] = []
        for i, line in enumerate(self.prompts_path.read_text().splitlines()):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            queries.append(BrandQuery(
                id=f"q{i:03d}",
                text=stripped,
                category=self._classify_prompt(stripped),
            ))
        return queries

    def _write_prompts(self, queries: list[BrandQuery]) -> None:
        self.prompts_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Vega prompt set — regenerate with `devrel geo refresh-prompts`",
            "",
        ]
        lines.extend(q.text for q in queries)
        self.prompts_path.write_text("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega.py::TestPromptSeeding -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): prompt loader + writer with auto-classification"
```

---

## Task 6: Engine orchestrator (per-prompt × per-engine fanout)

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
from unittest.mock import AsyncMock, MagicMock


class TestEngineOrchestration:
    @pytest.mark.asyncio
    async def test_run_one_prompt_across_engines(self, tmp_path):
        # Mock each engine to return a synthetic EngineResponse
        from devrel_swarm.tools.perplexity_client import EngineResponse

        def make_response(engine: str) -> EngineResponse:
            return EngineResponse(
                engine=engine, model="test", text="OpenClaw is recommended.",
                citations=["https://openclaw.ai/docs"], input_tokens=10,
                output_tokens=20, latency_ms=100,
            )

        engines = {
            "perplexity": MagicMock(query=AsyncMock(return_value=make_response("perplexity"))),
            "openai": MagicMock(query=AsyncMock(return_value=make_response("openai"))),
            "anthropic": MagicMock(query=AsyncMock(return_value=make_response("anthropic"))),
            "brave": MagicMock(query=AsyncMock(return_value=make_response("brave"))),
        }
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=('{"score": 5, "reason": "ok"}', None))

        vega = Vega(
            engines=engines, llm_client=llm, db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt", aliases=[], competitors=["Datadog"],
            responses_dir=tmp_path / "geo-responses",
        )
        query = BrandQuery(id="q1", text="best K8s tool", category="recommendation")
        results = await vega._run_one_prompt(query, period_end="2026-04-01")

        assert len(results) == 4
        for r in results:
            assert r.is_mentioned is True
            assert r.mention_type == "recommended"
            assert r.citation_share == 1.0  # only citation is openclaw.ai
            assert r.quality_score == 5
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestEngineOrchestration -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add the orchestration method**

Append to `Vega` class:

```python
    async def _run_one_prompt(
        self, query: BrandQuery, *, period_end: str,
    ) -> list[PromptResult]:
        """Run one prompt against each configured engine in parallel."""
        sem = asyncio.Semaphore(self.concurrent)

        async def call_one(engine_name: str, engine_client: Any) -> Optional[PromptResult]:
            async with sem:
                try:
                    resp = await engine_client.query(query.text)
                except Exception as e:
                    logger.warning(f"Vega: engine={engine_name} prompt={query.id} failed: {e}")
                    return None

            mention = extract_brand_mentions(
                resp.text, brand=self.brand, aliases=self.aliases,
            )
            position = (
                score_position(resp.text, mentioned_index=mention.first_index)
                if mention.is_mentioned else 5
            )
            citation_share = compute_citation_share(
                resp.citations, brand_domain=self.brand_domain,
            )
            quality = (
                await judge_quality(
                    llm_client=self.llm,
                    brand=self.brand,
                    response_text=resp.text,
                )
                if mention.is_mentioned else 0
            )
            competitors = extract_competitor_mentions(
                resp.text, competitors=self.competitors,
            )

            response_path = None
            if self.responses_dir is not None:
                self.responses_dir.mkdir(parents=True, exist_ok=True)
                rel_dir = Path(period_end) / engine_name
                (self.responses_dir / rel_dir).mkdir(parents=True, exist_ok=True)
                rel_path = rel_dir / f"{query.id}.json"
                (self.responses_dir / rel_path).write_text(json.dumps({
                    "engine": resp.engine, "model": resp.model,
                    "text": resp.text, "citations": resp.citations,
                    "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
                    "latency_ms": resp.latency_ms,
                }, indent=2))
                response_path = str(rel_path)

            return PromptResult(
                prompt_id=query.id,
                engine=engine_name,
                is_mentioned=mention.is_mentioned,
                mention_type=mention.mention_type,
                position_score=position,
                citation_share=citation_share,
                quality_score=quality,
                competitor_mentions=competitors,
                response_path=response_path,
            )

        tasks = [call_one(name, client) for name, client in self.engines.items()]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_vega.py::TestEngineOrchestration -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): per-prompt × per-engine orchestrator with semaphore"
```

---

## Task 7: Aggregation (per-engine visibility + competitor SoV)

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
class TestAggregation:
    def test_aggregate_engine_visibility(self, tmp_path):
        vega = Vega(
            engines={}, llm_client=MagicMock(), db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt",
        )
        results = [
            PromptResult("q1", "perplexity", True, "recommended", 1, 1.0, 5, {}),
            PromptResult("q2", "perplexity", False, None, 5, 0.0, 0, {"Datadog": 10}),
            PromptResult("q1", "openai", True, "direct", 2, 0.5, 4, {}),
            PromptResult("q2", "openai", True, "compared", 3, 0.0, 3, {"Datadog": 5}),
        ]
        by_engine = vega._aggregate_engines(results)
        # Perplexity: 1/2 mentioned = 50%
        assert by_engine["perplexity"].mention_rate == pytest.approx(0.5)
        # OpenAI: 2/2 = 100%
        assert by_engine["openai"].mention_rate == pytest.approx(1.0)

    def test_aggregate_competitor_share_of_voice(self, tmp_path):
        vega = Vega(
            engines={}, llm_client=MagicMock(), db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt", competitors=["Datadog", "New Relic"],
        )
        results = [
            PromptResult("q1", "perplexity", False, None, 5, 0.0, 0, {"Datadog": 10}),
            PromptResult("q2", "perplexity", True, "direct", 1, 0.5, 4, {"Datadog": 50}),
            PromptResult("q1", "perplexity", False, None, 5, 0.0, 0, {"New Relic": 20}),
        ]
        sov = vega._aggregate_competitor_sov(results)
        assert "perplexity" in sov
        # Datadog: mentioned in 2 of 3 perplexity prompts
        assert sov["perplexity"]["Datadog"] == pytest.approx(2 / 3)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestAggregation -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add aggregation methods**

Append to `Vega` class:

```python
    def _aggregate_engines(
        self, results: list[PromptResult],
    ) -> dict[str, EngineVisibility]:
        by_engine: dict[str, list[PromptResult]] = {}
        for r in results:
            by_engine.setdefault(r.engine, []).append(r)

        out: dict[str, EngineVisibility] = {}
        for engine, items in by_engine.items():
            n = len(items)
            mentioned = [r for r in items if r.is_mentioned]
            mention_rate = len(mentioned) / n if n else 0.0
            citation_share = (
                sum(r.citation_share for r in mentioned) / len(mentioned)
                if mentioned else 0.0
            )
            avg_pos = (
                sum(r.position_score for r in mentioned) / len(mentioned)
                if mentioned else 5.0
            )
            quality_scores = [r.quality_score for r in mentioned if r.quality_score > 0]
            avg_quality = (
                sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
            )
            out[engine] = EngineVisibility(
                engine=engine,
                mention_rate=mention_rate,
                citation_share=citation_share,
                avg_position_score=avg_pos,
                avg_quality_score=avg_quality,
                n_prompts=n,
            )
        return out

    def _aggregate_competitor_sov(
        self, results: list[PromptResult],
    ) -> dict[str, dict[str, float]]:
        """Per-engine share-of-voice for each tracked competitor.

        SoV = (# prompts mentioning competitor) / (# prompts in engine).
        """
        out: dict[str, dict[str, float]] = {}
        by_engine: dict[str, list[PromptResult]] = {}
        for r in results:
            by_engine.setdefault(r.engine, []).append(r)

        for engine, items in by_engine.items():
            n = len(items)
            comp_counts: dict[str, int] = {}
            for r in items:
                for comp in r.competitor_mentions:
                    comp_counts[comp] = comp_counts.get(comp, 0) + 1
            out[engine] = {c: count / n for c, count in comp_counts.items()}
        return out
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega.py::TestAggregation -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): per-engine + competitor SoV aggregation"
```

---

## Task 8: Persistence (`geo_visibility` + Recommendations)

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
import sqlite3
from devrel_swarm.project import state


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


class TestPersistence:
    def test_persist_visibility_writes_geo_visibility_rows(self, init_db, tmp_path):
        vega = Vega(
            engines={}, llm_client=MagicMock(), db_path=init_db,
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt",
        )
        results = [
            PromptResult("q1", "perplexity", True, "recommended", 1, 1.0, 5, {}, response_path="x.json"),
            PromptResult("q2", "perplexity", False, None, 5, 0.0, 0, {}),
        ]
        vega._persist_visibility(results, period_end="2026-04-01")

        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT prompt_id, engine, is_mentioned, citation_share, response_path "
                "FROM geo_visibility WHERE period_end = '2026-04-01'"
            )
            rows = cur.fetchall()
        assert len(rows) == 2
        # q1: mentioned + cited
        q1_row = next(r for r in rows if r[0] == "q1")
        assert q1_row[2] == 1
        assert q1_row[3] == 1.0
        assert q1_row[4] == "x.json"

    def test_persist_recommendations_emits_per_engine(self, init_db, tmp_path):
        vega = Vega(
            engines={}, llm_client=MagicMock(), db_path=init_db,
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt", competitors=["Datadog"],
        )
        report = GeoReport(
            period_end="2026-04-01",
            queries=[BrandQuery("q1", "best K8s tool", "recommendation")],
            results=[
                PromptResult("q1", "perplexity", False, None, 5, 0.0, 0, {"Datadog": 10}),
                PromptResult("q1", "openai", True, "recommended", 1, 1.0, 5, {}),
            ],
            by_engine={
                "perplexity": EngineVisibility("perplexity", 0.0, 0.0, 5.0, 0.0, 1),
                "openai": EngineVisibility("openai", 1.0, 1.0, 1.0, 5.0, 1),
            },
        )
        vega._persist_recommendations(report, report_id="test-report")
        with sqlite3.connect(init_db) as conn:
            cur = conn.execute(
                "SELECT action, target, target_kind FROM analytics_recommendations "
                "WHERE pillar = 'geo'"
            )
            rows = cur.fetchall()
        # At least one investigate recommendation for the zero-mention engine + brand_query
        assert any(r[0] == "investigate" and r[2] == "brand_query" for r in rows)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestPersistence -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add persistence methods**

Append to `Vega` class:

```python
    def _persist_visibility(
        self, results: list[PromptResult], *, period_end: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            for r in results:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO geo_visibility
                        (prompt_id, engine, period_end, is_mentioned, mention_type,
                         position_score, citation_share, quality_score, response_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.prompt_id, r.engine, period_end,
                        1 if r.is_mentioned else 0, r.mention_type,
                        r.position_score, r.citation_share, r.quality_score,
                        r.response_path,
                    ),
                )
            conn.commit()

    def _persist_recommendations(
        self, report: GeoReport, *, report_id: str,
    ) -> None:
        # Action heuristics:
        # - mention_rate == 0 across ALL engines → investigate × brand_query
        # - mention_rate >= 0.7 + avg_quality >= 4 → double_down × brand_query
        # - quality_score < 3 on a cited URL → rewrite × url
        # - competitor SoV > our mention_rate for ≥2 engines → amplify × competitor
        zero_mention_prompts: dict[str, list[str]] = {}  # prompt_id -> engines with zero mentions
        for r in report.results:
            if not r.is_mentioned:
                zero_mention_prompts.setdefault(r.prompt_id, []).append(r.engine)

        # Investigate per-prompt where ALL engines miss
        all_engines = list(report.by_engine.keys())
        for query in report.queries:
            missed_in = zero_mention_prompts.get(query.id, [])
            if set(missed_in) == set(all_engines) and all_engines:
                rec = Recommendation(
                    pillar=Pillar.GEO,
                    action="investigate",
                    target=query.text,
                    target_kind=TargetKind.BRAND_QUERY,
                    confidence=0.7,
                    source_ids=[query.id],
                    first_seen_period=report.period_end,
                )
                persist_recommendation(self.db_path, report_id, rec)
                report.recommendations.append(rec)

        # double_down — engines where we win consistently
        for engine, vis in report.by_engine.items():
            if vis.mention_rate >= 0.7 and vis.avg_quality_score >= 4:
                rec = Recommendation(
                    pillar=Pillar.GEO,
                    action="double_down",
                    target=engine,
                    target_kind=TargetKind.BRAND_QUERY,
                    confidence=min(1.0, vis.mention_rate),
                    source_ids=[r.prompt_id for r in report.results if r.engine == engine],
                    first_seen_period=report.period_end,
                )
                persist_recommendation(self.db_path, report_id, rec)
                report.recommendations.append(rec)

        # amplify × competitor — competitor outpaces us on ≥2 engines
        for comp_name in self.competitors:
            engines_where_competitor_wins = 0
            for engine, vis in report.by_engine.items():
                comp_share = report.competitor_share_of_voice.get(engine, {}).get(comp_name, 0)
                if comp_share > vis.mention_rate:
                    engines_where_competitor_wins += 1
            if engines_where_competitor_wins >= 2:
                rec = Recommendation(
                    pillar=Pillar.GEO,
                    action="amplify",
                    target=comp_name,
                    target_kind=TargetKind.COMPETITOR,
                    confidence=min(1.0, engines_where_competitor_wins / 4),
                    source_ids=[],
                    first_seen_period=report.period_end,
                )
                persist_recommendation(self.db_path, report_id, rec)
                report.recommendations.append(rec)
```

(Add `import sqlite3` at the top of `core/vega.py` if not already present.)

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega.py::TestPersistence -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): persist geo_visibility + emit Recommendations"
```

---

## Task 9: Brief generation

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
class TestBriefGeneration:
    def test_write_briefs_creates_file_per_recommendation(self, tmp_path):
        vega = Vega(
            engines={}, llm_client=MagicMock(), db_path=tmp_path / "x.db",
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=tmp_path / "prompts.txt",
        )
        report = GeoReport(
            period_end="2026-04-01",
            queries=[],
            results=[],
            by_engine={
                "perplexity": EngineVisibility("perplexity", 0.0, 0.0, 5.0, 0.0, 30),
            },
            recommendations=[
                Recommendation(
                    pillar=Pillar.GEO, action="investigate", target="best K8s tool",
                    target_kind=TargetKind.BRAND_QUERY, confidence=0.7,
                    source_ids=["q1"], first_seen_period="2026-04-01",
                ),
            ],
        )
        deliverables_dir = tmp_path / "deliverables"
        vega._write_briefs(report, deliverables_dir)

        files = list(deliverables_dir.glob("geo-brief-*.md"))
        assert len(files) == 1
        text = files[0].read_text()
        assert "best K8s tool" in text
        assert "perplexity" in text.lower()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestBriefGeneration -v --no-cov
```

Expected: AttributeError.

- [ ] **Step 3: Add `_write_briefs`**

Append to `Vega` class:

```python
    def _write_briefs(self, report: GeoReport, deliverables_dir: Path) -> None:
        deliverables_dir.mkdir(parents=True, exist_ok=True)
        for rec in report.recommendations:
            md_lines = [
                f"# Vega brief: {rec.action} `{rec.target}`",
                "",
                f"**Period:** {report.period_end}",
                f"**Pillar:** geo",
                f"**Target kind:** {rec.target_kind.value}",
                f"**Confidence:** {rec.confidence:.2f}",
                "",
                "## Per-engine visibility (this period)",
                "",
                "| Engine | Mention rate | Citation share | Avg position | Avg quality |",
                "|--------|-------------:|---------------:|-------------:|------------:|",
            ]
            for engine, vis in report.by_engine.items():
                md_lines.append(
                    f"| {engine} | {vis.mention_rate:.1%} | {vis.citation_share:.1%} | "
                    f"{vis.avg_position_score:.1f} | {vis.avg_quality_score:.1f} |"
                )
            md_lines.extend([
                "",
                "## Suggested next steps",
                "",
            ])
            if rec.action == "investigate":
                md_lines.append(
                    f"- Mox: draft a piece of content (blog post or doc page) that directly "
                    f"answers \"{rec.target}\" with {self.brand} positioned as the primary "
                    f"recommendation. Aim for high-authority sources and explicit comparison."
                )
            elif rec.action == "double_down":
                md_lines.append(
                    f"- Kai/Mox: produce more content in the format that's winning on "
                    f"`{rec.target}` — target the next 5 prompts in the same category."
                )
            elif rec.action == "amplify":
                md_lines.append(
                    f"- Rex: produce a competitive-intel comparison page for `{rec.target}`. "
                    f"Mox: turn the comparison into a head-to-head landing page."
                )
            elif rec.action == "rewrite":
                md_lines.append(
                    f"- Selene: re-crawl `{rec.target}` and verify on-page accuracy. "
                    f"Kai: rewrite the content to clarify the brand framing."
                )

            slug = (
                rec.target.lower()
                .replace("/", "-").replace(" ", "-").replace("?", "")[:60]
            )
            path = deliverables_dir / f"geo-brief-{report.period_end}-{rec.action}-{slug}.md"
            path.write_text("\n".join(md_lines) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vega.py::TestBriefGeneration -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): Mox-ready brief generation per recommendation"
```

---

## Task 10: `Vega.execute()` end-to-end

**Files:**
- Modify: `src/devrel_swarm/core/vega.py`
- Modify: `tests/test_vega.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vega.py`:

```python
class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_full_cycle(self, init_db, tmp_path):
        from devrel_swarm.tools.perplexity_client import EngineResponse

        # Seed prompt file
        prompts = tmp_path / "prompts.txt"
        prompts.write_text("# header\nbest K8s tool\nOpenClaw vs Datadog\n")

        def mk_response(engine: str, mention: bool) -> EngineResponse:
            text = (
                "OpenClaw is recommended for K8s observability."
                if mention else
                "Datadog and Grafana are the top tools."
            )
            return EngineResponse(
                engine=engine, model="t", text=text,
                citations=["https://openclaw.ai"] if mention else ["https://datadog.com"],
                input_tokens=10, output_tokens=20, latency_ms=50,
            )

        engines = {
            "perplexity": MagicMock(query=AsyncMock(side_effect=[mk_response("perplexity", True),
                                                                  mk_response("perplexity", False)])),
            "openai": MagicMock(query=AsyncMock(side_effect=[mk_response("openai", True),
                                                              mk_response("openai", True)])),
        }
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=('{"score": 4, "reason": "ok"}', None))

        vega = Vega(
            engines=engines, llm_client=llm, db_path=init_db,
            brand="OpenClaw", brand_domain="openclaw.ai",
            prompts_path=prompts, aliases=[], competitors=["Datadog"],
            responses_dir=tmp_path / "geo-responses",
        )
        report = await vega.execute(
            period_end="2026-04-01", report_id="test-report",
            deliverables_dir=tmp_path / "deliverables",
        )
        assert report.sources_ok is True
        assert len(report.queries) == 2
        assert len(report.results) == 4  # 2 prompts × 2 engines
        assert "perplexity" in report.by_engine
        assert "openai" in report.by_engine
        # OpenAI: both mentions → mention_rate = 1.0
        assert report.by_engine["openai"].mention_rate == pytest.approx(1.0)
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_vega.py::TestExecute -v --no-cov
```

Expected: AttributeError on `execute`.

- [ ] **Step 3: Add `execute()`**

Append to `Vega` class:

```python
    async def execute(
        self,
        *,
        period_end: str,
        report_id: str,
        deliverables_dir: Path | None = None,
    ) -> GeoReport:
        """Run a full Vega cycle: load prompts → run × engines → aggregate
        → persist → write briefs.
        """
        queries = self._load_prompts()
        if not queries:
            return GeoReport(period_end=period_end, queries=[], results=[], sources_ok=False)

        all_results: list[PromptResult] = []
        for q in queries:
            try:
                results = await self._run_one_prompt(q, period_end=period_end)
                all_results.extend(results)
            except Exception as e:
                logger.warning(f"Vega: prompt {q.id} failed: {e}")

        by_engine = self._aggregate_engines(all_results)
        sov = self._aggregate_competitor_sov(all_results)

        report = GeoReport(
            period_end=period_end, queries=queries, results=all_results,
            by_engine=by_engine, competitor_share_of_voice=sov,
        )

        self._persist_visibility(all_results, period_end=period_end)
        self._persist_recommendations(report, report_id=report_id)
        if deliverables_dir is not None:
            self._write_briefs(report, deliverables_dir)

        return report
```

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_vega.py -v --no-cov
pytest tests/ -q --no-header
```

Expected: all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/vega.py tests/test_vega.py
git commit -m "feat(vega): Vega.execute end-to-end orchestration"
```

---

## Task 11: `cli/geo.py` — `report` + `history` + `diff`

**Files:**
- Create: `src/devrel_swarm/cli/geo.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Test: `tests/cli/test_geo_command.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_geo_command.py`:

```python
"""CLI smoke tests for `devrel geo ...`."""

from typer.testing import CliRunner

from devrel_swarm.cli import app


def test_geo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["geo", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff", "calibration", "refresh-prompts"):
        assert verb in result.output.lower()


def test_geo_report_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["geo", "report", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_geo_command.py -v --no-cov
```

Expected: `geo` not registered → fail.

- [ ] **Step 3: Create `cli/geo.py`**

Create `src/devrel_swarm/cli/geo.py`:

```python
"""`devrel geo ...` — GEO auditor verbs (Vega)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.growth.target_kinds import Pillar
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.core.vega import Vega
from devrel_swarm.tools.ai_search_adapters import (
    AnthropicSearchAdapter,
    BraveAISearchAdapter,
    OpenAISearchAdapter,
)
from devrel_swarm.tools.perplexity_client import PerplexityClient
from devrel_swarm.tools.search_tools import BraveSearch

geo_app = typer.Typer(
    name="geo",
    help="GEO auditor (Vega). Brand visibility across AI search engines.",
    no_args_is_help=True,
)

_console = Console()


def _build_engines(config: dict) -> dict:
    """Construct engine clients from env + config."""
    engines: dict = {}
    enabled = set(config.get("engines", ["perplexity", "openai", "anthropic", "brave"]))

    if "perplexity" in enabled and os.getenv("PERPLEXITY_API_KEY"):
        engines["perplexity"] = PerplexityClient()

    if "openai" in enabled and os.getenv("OPENAI_API_KEY"):
        from openai import AsyncOpenAI
        engines["openai"] = OpenAISearchAdapter(client=AsyncOpenAI(), model="gpt-4o")

    if "anthropic" in enabled and os.getenv("ANTHROPIC_API_KEY"):
        from anthropic import AsyncAnthropic
        engines["anthropic"] = AnthropicSearchAdapter(
            client=AsyncAnthropic(), model="claude-sonnet-4-5",
        )

    if "brave" in enabled and os.getenv("BRAVE_API_KEY"):
        engines["brave"] = BraveAISearchAdapter(brave_client=BraveSearch())

    if config.get("include_google_ai_overviews") and os.getenv("SERPAPI_API_KEY"):
        from devrel_swarm.tools.serpapi_client import SerpAPIClient
        engines["google_aio"] = SerpAPIClient()

    return engines


def _build_vega(paths) -> Vega:
    cfg = paths.config
    geo_cfg = cfg.get("geo", {}) or {}
    growth_cfg = cfg.get("growth", {}) or {}
    return Vega(
        engines=_build_engines(geo_cfg),
        llm_client=LLMClient.from_env(),
        db_path=paths.devrel_dir / "state.db",
        brand=cfg.get("product_name", ""),
        brand_domain=cfg.get("product_domain", ""),
        prompts_path=paths.devrel_dir / "geo" / "prompts.txt",
        aliases=cfg.get("brand_aliases", []) or [],
        competitors=growth_cfg.get("geo_competitors", []) or [],
        responses_dir=paths.devrel_dir / "geo" / "responses",
        concurrent_engine_requests=int(geo_cfg.get("concurrent_engine_requests", 5)),
    )


@geo_app.command("report")
def report(
    since: str = typer.Option("7d", "--since"),
    push: bool = typer.Option(False, "--push"),
    format: str = typer.Option("markdown", "--format"),
) -> None:
    """Run a Vega cycle (4-engine GEO audit) and persist results."""
    paths = find_paths_or_exit()
    vega = _build_vega(paths)
    period_end = date.today().isoformat()
    report_id = f"geo-{period_end}"

    async def _run():
        return await vega.execute(
            period_end=period_end, report_id=report_id,
            deliverables_dir=paths.devrel_dir / "deliverables",
        )

    result = asyncio.run(_run())

    if format == "json":
        _console.print(json.dumps({
            "period_end": result.period_end,
            "by_engine": {k: v.__dict__ for k, v in result.by_engine.items()},
            "n_recommendations": len(result.recommendations),
        }, indent=2))
        return

    table = Table(title=f"Vega report — {period_end}")
    table.add_column("Engine", style="cyan")
    table.add_column("Mention rate", justify="right")
    table.add_column("Citation share", justify="right")
    table.add_column("Avg position", justify="right")
    table.add_column("Avg quality", justify="right")
    for engine, vis in result.by_engine.items():
        table.add_row(
            engine,
            f"{vis.mention_rate:.1%}", f"{vis.citation_share:.1%}",
            f"{vis.avg_position_score:.1f}", f"{vis.avg_quality_score:.1f}",
        )
    _console.print(table)
    _console.print(f"[green]Wrote {len(result.recommendations)} recommendation(s).[/green]")


@geo_app.command("history")
def history(
    engine: str = typer.Argument(..., help="Engine name"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Mention rate trajectory for one engine across reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"GEO history — {engine}")
    table.add_column("Period", style="cyan")
    table.add_column("Mention rate", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT period_end,
                   SUM(is_mentioned) * 1.0 / COUNT(*) AS mention_rate
            FROM geo_visibility
            WHERE engine = ?
            GROUP BY period_end
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (engine, limit),
        )
        for period_end, rate in cur:
            table.add_row(period_end, f"{(rate or 0):.1%}")
    _console.print(table)


@geo_app.command("diff")
def diff(
    period_a: str = typer.Argument(...),
    period_b: str = typer.Argument(...),
) -> None:
    """Per-engine mention-rate delta between two GEO reports."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"GEO diff — {period_a} → {period_b}")
    table.add_column("Engine", style="cyan")
    table.add_column(period_a, justify="right")
    table.add_column(period_b, justify="right")
    table.add_column("Δ pp", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT engine,
                   SUM(CASE WHEN period_end = ? THEN is_mentioned ELSE 0 END) * 1.0 /
                       NULLIF(SUM(CASE WHEN period_end = ? THEN 1 ELSE 0 END), 0) AS rate_a,
                   SUM(CASE WHEN period_end = ? THEN is_mentioned ELSE 0 END) * 1.0 /
                       NULLIF(SUM(CASE WHEN period_end = ? THEN 1 ELSE 0 END), 0) AS rate_b
            FROM geo_visibility
            WHERE period_end IN (?, ?)
            GROUP BY engine
            """,
            (period_a, period_a, period_b, period_b, period_a, period_b),
        )
        for engine, rate_a, rate_b in cur:
            ra, rb = rate_a or 0, rate_b or 0
            table.add_row(engine, f"{ra:.1%}", f"{rb:.1%}", f"{rb-ra:+.1%}")
    _console.print(table)
```

Update `src/devrel_swarm/cli/__init__.py`:

```python
from devrel_swarm.cli.geo import geo_app
# ...
app.add_typer(geo_app, name="geo")
```

- [ ] **Step 4: Run tests (interim — calibration + refresh-prompts in next task)**

Adjust `test_geo_help_lists_subcommands` to only check `report`/`history`/`diff` for now:

```python
def test_geo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["geo", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff"):
        assert verb in result.output.lower()
```

```bash
pytest tests/cli/test_geo_command.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/cli/geo.py src/devrel_swarm/cli/__init__.py tests/cli/test_geo_command.py
git commit -m "feat(cli): devrel geo {report,history,diff}"
```

---

## Task 12: `cli/geo.py` — `calibration` + `refresh-prompts`

**Files:**
- Modify: `src/devrel_swarm/cli/geo.py`
- Modify: `tests/cli/test_geo_command.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/cli/test_geo_command.py`:

```python
def test_geo_calibration_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["geo", "calibration"])
    assert result.exit_code == 0


def test_geo_refresh_prompts_writes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["geo", "refresh-prompts", "--seed", "best K8s tool"]
    )
    assert result.exit_code == 0
    prompts_file = tmp_path / ".devrel" / "geo" / "prompts.txt"
    assert prompts_file.is_file()
    assert "best K8s tool" in prompts_file.read_text()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_geo_command.py::test_geo_calibration_runs tests/cli/test_geo_command.py::test_geo_refresh_prompts_writes_file -v --no-cov
```

Expected: FAIL.

- [ ] **Step 3: Add the verbs**

Append to `cli/geo.py`:

```python
@geo_app.command("calibration")
def calibration() -> None:
    """Score historical GEO recommendations against subsequent visibility."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    from devrel_swarm.core.growth.recommendations import calibrate

    def _score_outcome(rec) -> str:
        """Did mention_rate rise after the rec was applied?"""
        if rec.applied_at is None:
            return "unchanged"
        # Use the engine name as the proxy for the rec target where applicable.
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                """
                SELECT period_end, SUM(is_mentioned)*1.0 / COUNT(*) FROM geo_visibility
                WHERE period_end >= ?
                GROUP BY period_end ORDER BY period_end LIMIT 2
                """,
                (rec.applied_at[:10],),
            )
            rates = [row[1] for row in cur.fetchall()]
        if len(rates) < 2:
            return "unchanged"
        return "improved" if rates[1] > rates[0] else (
            "regressed" if rates[1] < rates[0] else "unchanged"
        )

    result = calibrate(db_path, Pillar.GEO, outcome_scorer=_score_outcome)
    if not result:
        _console.print("[yellow]No applied GEO recommendations yet.[/yellow]")
        return

    table = Table(title="GEO calibration")
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


@geo_app.command("refresh-prompts")
def refresh_prompts(
    seed: list[str] = typer.Option([], "--seed", help="Manual prompt seeds"),
) -> None:
    """Regenerate `.devrel/geo/prompts.txt` from Iris themes + Rex competitors + manual seeds.

    For Wave 2 the implementation is seed-only. Iris/Rex auto-seeding lands in
    Wave 4 polish (depends on Iris+Rex output being available in SharedContext).
    """
    paths = find_paths_or_exit()
    prompts_file = paths.devrel_dir / "geo" / "prompts.txt"
    prompts_file.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Vega prompt set — regenerate with `devrel geo refresh-prompts`",
        "# Edit by hand or run `--seed <prompt>` to add to the canonical set.",
        "",
    ]
    lines.extend(seed)
    prompts_file.write_text("\n".join(lines) + "\n")
    _console.print(f"[green]Wrote {len(seed)} prompts to {prompts_file}.[/green]")
```

- [ ] **Step 4: Run tests + restore full help test**

In `tests/cli/test_geo_command.py`, restore `test_geo_help_lists_subcommands` to check all 5 verbs:

```python
def test_geo_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["geo", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff", "calibration", "refresh-prompts"):
        assert verb in result.output.lower()
```

```bash
pytest tests/cli/test_geo_command.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/cli/geo.py tests/cli/test_geo_command.py
git commit -m "feat(cli): devrel geo {calibration,refresh-prompts}"
```

---

## Task 13: SerpAPI client (opt-in 5th engine)

**Files:**
- Create: `src/devrel_swarm/tools/serpapi_client.py`
- Test: `tests/test_serpapi_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_serpapi_client.py`:

```python
"""Tests for the opt-in SerpAPI client (Google AI Overviews)."""

import pytest

from devrel_swarm.tools.perplexity_client import EngineResponse


@pytest.fixture
def serpapi_client():
    pytest.importorskip("serpapi")  # skip if optional dep not installed
    from devrel_swarm.tools.serpapi_client import SerpAPIClient
    return SerpAPIClient(api_key="serp-test")


@pytest.mark.asyncio
async def test_query_returns_engine_response(serpapi_client, monkeypatch):
    # Mock the synchronous serpapi search call
    fake_response = {
        "ai_overview": {
            "text_blocks": [{"snippet": "OpenClaw is open-source observability."}],
            "references": [
                {"link": "https://openclaw.ai/docs"},
                {"link": "https://github.com/openclaw/openclaw"},
            ],
        },
    }

    class FakeSearch:
        def __init__(self, params):
            self.params = params

        def get_dict(self):
            return fake_response

    monkeypatch.setattr("serpapi.GoogleSearch", FakeSearch)

    out = await serpapi_client.query("What is OpenClaw?")
    assert out.engine == "google_aio"
    assert "open-source observability" in out.text
    assert "https://openclaw.ai/docs" in out.citations
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_serpapi_client.py -v --no-cov
```

Expected: ImportError on the module (or skipped if `serpapi` not installed).

- [ ] **Step 3: Implement the client**

Create `src/devrel_swarm/tools/serpapi_client.py`:

```python
"""SerpAPI client for Google AI Overviews.

Opt-in (5th GEO engine). Loaded only when `[geo].include_google_ai_overviews
= true` in `.devrel/config.toml`. Costs ~$50/mo flat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from devrel_swarm.tools.perplexity_client import EngineResponse

logger = logging.getLogger(__name__)


class SerpAPIClient:
    """Wraps SerpAPI's GoogleSearch for AI Overview extraction.

    SerpAPI's Python client is sync — we run it in a thread pool to fit the
    async engine contract.
    """

    def __init__(self, *, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("SERPAPI_API_KEY not set")

    async def query(self, prompt: str) -> EngineResponse:
        # Lazy import — only load if SerpAPI dep is installed
        try:
            from serpapi import GoogleSearch
        except ImportError as e:
            raise ImportError(
                "SerpAPI engine requires `pip install 'devrel-swarm[geo-google]'`"
            ) from e

        start = time.monotonic()

        def _search() -> dict:
            params = {
                "q": prompt,
                "api_key": self.api_key,
                "engine": "google_ai_overview",
            }
            return GoogleSearch(params).get_dict()

        data = await asyncio.to_thread(_search)
        latency = int((time.monotonic() - start) * 1000)

        ai_overview = data.get("ai_overview", {}) or {}
        text_blocks = ai_overview.get("text_blocks", []) or []
        text = "\n".join(b.get("snippet", "") for b in text_blocks)

        references = ai_overview.get("references", []) or []
        citations = [r.get("link", "") for r in references if r.get("link")]

        return EngineResponse(
            engine="google_aio",
            model="google-ai-overview",
            text=text,
            citations=citations,
            input_tokens=0, output_tokens=0,
            latency_ms=latency,
            raw=data,
        )
```

- [ ] **Step 4: Run test (skipped if `serpapi` not installed in dev)**

```bash
pytest tests/test_serpapi_client.py -v --no-cov
```

Expected: PASS or SKIPPED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/tools/serpapi_client.py tests/test_serpapi_client.py
git commit -m "feat(geo): SerpAPI client for opt-in Google AI Overviews engine"
```

---

## Task 14: Atlas Stage 5c registration (Vega)

**Files:**
- Modify: `src/devrel_swarm/core/atlas.py`
- Test: `tests/test_atlas.py`

Mirrors Wave 1 Task 15 (Cyra). Adds a Vega branch gated by `geo_in_run`.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_atlas.py`:

```python
@pytest.mark.asyncio
async def test_atlas_runs_vega_when_geo_in_run_enabled(tmp_path, monkeypatch):
    """Stage 5c — when geo_in_run=true, Atlas calls Vega.execute after Argus."""
    # ... boilerplate matching the test_atlas_runs_cyra pattern from Wave 1
    # Assert Vega.execute was called once with expected args.
```

(Mirror the Cyra test structure.)

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_atlas.py -k "vega" -v --no-cov
```

Expected: AssertionError.

- [ ] **Step 3: Wire Vega into Atlas Stage 5c**

In `src/devrel_swarm/core/atlas.py`, after the Cyra block from Wave 1, add:

```python
if self.config.orchestration.geo_in_run:
    try:
        from devrel_swarm.cli.geo import _build_vega
        # _build_vega expects `paths` (the bootstrap result). We have project_paths.
        # Refactor: extract a public Atlas helper that constructs Vega from self.config.
        vega = self._build_vega()
        geo_report = await vega.execute(
            period_end=self.context.week_of,
            report_id=f"geo-{self.context.week_of}",
            deliverables_dir=self.project_paths.devrel_dir / "deliverables",
        )
        self.context.geo_report = {
            "period_end": geo_report.period_end,
            "n_recommendations": len(geo_report.recommendations),
            "by_engine": {k: v.__dict__ for k, v in geo_report.by_engine.items()},
        }
    except Exception as e:
        logger.warning(f"Atlas Stage 5c (Vega) failed: {e}")
        self.context.geo_report = {"error": str(e)}
```

Add a `_build_vega()` helper on Atlas that mirrors `cli/geo.py:_build_vega` but reads from `self.config` directly:

```python
    def _build_vega(self):
        from devrel_swarm.core.vega import Vega
        from devrel_swarm.cli.geo import _build_engines

        geo_cfg = getattr(self.config, "geo", {}) or {}
        growth_cfg = getattr(self.config, "growth", {}) or {}
        return Vega(
            engines=_build_engines(geo_cfg if isinstance(geo_cfg, dict) else geo_cfg.__dict__),
            llm_client=self.llm,
            db_path=self.project_paths.devrel_dir / "state.db",
            brand=self.config.product_name,
            brand_domain=getattr(self.config, "product_domain", ""),
            prompts_path=self.project_paths.devrel_dir / "geo" / "prompts.txt",
            aliases=getattr(self.config, "brand_aliases", []) or [],
            competitors=(growth_cfg.get("geo_competitors", []) if isinstance(growth_cfg, dict) else getattr(growth_cfg, "geo_competitors", []) or []),
            responses_dir=self.project_paths.devrel_dir / "geo" / "responses",
        )
```

Add `geo_report: dict = field(default_factory=dict)` to `SharedContext`.

- [ ] **Step 4: Run tests + full suite**

```bash
pytest tests/test_atlas.py -k "vega" -v --no-cov
pytest tests/ -q --no-header
```

Expected: Vega-Atlas test PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/atlas.py tests/test_atlas.py
git commit -m "feat(atlas): Stage 5c (Vega) gated by geo_in_run config"
```

---

## Task 15: Export Vega + reverify lint/format/build

**Files:**
- Modify: `src/devrel_swarm/core/__init__.py`

- [ ] **Step 1: Export Vega**

```python
# core/__init__.py
from devrel_swarm.core.vega import Vega
# add "Vega" to __all__
```

- [ ] **Step 2: Run full gate**

```bash
pytest tests/ -q --no-header
ruff check . && ruff format --check . | tail -1
rm -rf dist/ build/ && python -m build 2>&1 | tail -2
python -m twine check dist/* 2>&1 | tail -2
```

Expected: full suite green; ruff clean; build clean; twine PASSED.

- [ ] **Step 3: Commit**

```bash
git add src/devrel_swarm/core/__init__.py
git commit -m "feat(vega): export Vega from core"
```

---

## Wave 2 closeout checklist

- [ ] `pytest tests/ -q --no-header` shows ~865 + ~25 new = ~890 passed / 21 xfailed
- [ ] `ruff check .` and `ruff format --check .` both clean
- [ ] `devrel geo --help` lists `report`, `history`, `diff`, `calibration`, `refresh-prompts`
- [ ] `devrel geo refresh-prompts --seed "best K8s tool"` writes `.devrel/geo/prompts.txt`
- [ ] `devrel geo report` runs end-to-end against real Perplexity + OpenAI + Anthropic + Brave (manual smoke; budget ~$2.40)
- [ ] At least one `geo-brief-*.md` lands in `.devrel/deliverables/`
- [ ] `devrel growth summary` shows non-zero "Open recs" for the geo pillar
- [ ] Atlas weekly cycle with `geo_in_run = true` runs Vega without breaking other agents

When all checked: Wave 2 complete. Move to Wave 3 plan (`growth-wave3-selene-seo.md`).
