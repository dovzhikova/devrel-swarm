# P0: Wire Agent Execute Methods — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 4 specialist agents' `execute()` methods call their existing helper methods and the Anthropic API, so the full Mon→Fri pipeline produces real cascading data.

**Architecture:** Create a shared `agents/llm.py` wrapper around `anthropic.AsyncAnthropic` that all agents import. Sage is rule-based (no LLM needed — just wire to GitHubTools). Iris uses LLM for theme extraction. Nova is stats-based (no LLM — wire to its own methods). Kai uses LLM for content generation. Config loader reads `config/agent_config.yaml`.

**Tech Stack:** Python 3.12, anthropic SDK, httpx, dataclasses, pytest + respx

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agents/llm.py` | **Create** | Shared Anthropic client wrapper with `generate()` method |
| `agents/sage.py` | **Modify** | Wire `execute()` → `GitHubTools` + `triage_issue()` |
| `agents/iris.py` | **Modify** | Wire `execute()` → LLM theme extraction + real journey mapping |
| `agents/nova.py` | **Modify** | Wire `execute()` → `design_experiment()` + `analyze_funnel()` |
| `agents/kai.py` | **Modify** | Wire `execute()` → LLM content generation |
| `agents/config.py` | **Create** | Load `config/agent_config.yaml` |
| `agents/atlas.py` | **Modify** | Accept `github_tools` + `llm_client`, pass to agents |
| `tests/test_llm.py` | **Create** | Tests for LLM wrapper |
| `tests/test_sage.py` | **Modify** | Add tests for wired `execute()` |
| `tests/test_iris.py` | **Modify** | Add tests for LLM-backed theme extraction |
| `tests/test_nova.py` | **Modify** | Add tests for wired `execute()` |
| `tests/test_kai.py` | **Create** | Tests for LLM-backed content generation |
| `tests/test_config.py` | **Create** | Tests for config loader |
| `tests/conftest.py` | **Modify** | Add `mock_llm_client` and `mock_github_tools` fixtures |

---

## Chunk 1: Shared LLM Client + Config Loader

### Task 1: Create shared LLM client wrapper

**Files:**
- Create: `agents/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test for LLMClient**

```python
# tests/test_llm.py
"""Tests for shared LLM client wrapper."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.llm import LLMClient


class TestLLMClient:
    """Test LLMClient.generate() wrapper."""

    @pytest.mark.asyncio
    async def test_generate_returns_text(self):
        client = LLMClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Generated content here")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch.object(client._client.messages, "create", new_callable=AsyncMock, return_value=mock_response):
            result = await client.generate(
                system_prompt="You are a helper.",
                user_prompt="Write something.",
            )
        assert result == "Generated content here"

    @pytest.mark.asyncio
    async def test_generate_with_custom_model(self):
        client = LLMClient(api_key="test-key", model="claude-haiku-4-5-20251001")
        assert client.model == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_generate_json_mode(self):
        client = LLMClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"themes": []}')]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 20

        with patch.object(client._client.messages, "create", new_callable=AsyncMock, return_value=mock_response):
            result = await client.generate(
                system_prompt="Extract JSON.",
                user_prompt="Analyze this.",
            )
        assert '"themes"' in result

    def test_default_model(self):
        client = LLMClient(api_key="test-key")
        assert client.model == "claude-sonnet-4-5-20250929"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.llm'`

- [ ] **Step 3: Implement LLMClient**

```python
# agents/llm.py
"""Shared Anthropic LLM client wrapper for all agents."""

import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_MAX_TOKENS = 4096


class LLMClient:
    """Thin async wrapper around Anthropic's messages API.

    Usage::

        llm = LLMClient(api_key="sk-ant-...")
        text = await llm.generate(
            system_prompt="You are a helpful assistant.",
            user_prompt="Explain feature flags.",
        )
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self._client = AsyncAnthropic(api_key=api_key or "dummy")

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Send a prompt and return the response text."""
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text
        logger.info(
            f"LLM call: {response.usage.input_tokens} in, "
            f"{response.usage.output_tokens} out"
        )
        return text

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_llm.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/llm.py tests/test_llm.py
git commit -m "feat: add shared LLM client wrapper (agents/llm.py)"
```

---

### Task 2: Create config loader

**Files:**
- Create: `agents/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test for config loader**

```python
# tests/test_config.py
"""Tests for agent config loader."""

import pytest
from pathlib import Path
from agents.config import AgentConfig, load_config


class TestLoadConfig:
    """Test loading agent_config.yaml."""

    def test_load_from_file(self, tmp_path):
        config_file = tmp_path / "agent_config.yaml"
        config_file.write_text("""
agents:
  sage:
    enabled: true
  iris:
    enabled: false
orchestration:
  workflow_order:
    - sage
    - iris
retry_settings:
  max_retries: 5
  initial_delay_seconds: 10
  backoff_multiplier: 3.0
  max_delay_seconds: 120
""")
        config = load_config(config_file)
        assert config.retry_settings["max_retries"] == 5
        assert config.workflow_order == ["sage", "iris"]
        assert config.agents["sage"]["enabled"] is True

    def test_load_default_when_missing(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.retry_settings["max_retries"] == 3
        assert len(config.workflow_order) == 4

    def test_real_config_file_loads(self):
        real_path = Path(__file__).parent.parent / "config" / "agent_config.yaml"
        if real_path.exists():
            config = load_config(real_path)
            assert "sage" in config.agents
            assert "kai" in config.agents


class TestAgentConfig:
    """Test AgentConfig defaults."""

    def test_defaults(self):
        config = AgentConfig()
        assert config.retry_settings["max_retries"] == 3
        assert config.retry_settings["backoff_multiplier"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement config loader**

```python
# agents/config.py
"""Agent configuration loader from YAML."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_WORKFLOW_ORDER = ["sage", "iris", "nova", "kai"]
DEFAULT_RETRY = {
    "max_retries": 3,
    "initial_delay_seconds": 5,
    "backoff_multiplier": 2.0,
    "max_delay_seconds": 60,
}


@dataclass
class AgentConfig:
    """Parsed agent configuration."""

    agents: dict[str, Any] = field(default_factory=dict)
    workflow_order: list[str] = field(default_factory=lambda: list(DEFAULT_WORKFLOW_ORDER))
    retry_settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RETRY))
    api_clients: dict[str, Any] = field(default_factory=dict)
    logging_config: dict[str, Any] = field(default_factory=dict)


def load_config(path: Path) -> AgentConfig:
    """Load config from YAML file, falling back to defaults."""
    if not path.exists():
        logger.warning(f"Config not found at {path}, using defaults")
        return AgentConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    return AgentConfig(
        agents=raw.get("agents", {}),
        workflow_order=raw.get("orchestration", {}).get("workflow_order", DEFAULT_WORKFLOW_ORDER),
        retry_settings={**DEFAULT_RETRY, **raw.get("retry_settings", {})},
        api_clients=raw.get("api_clients", {}),
        logging_config=raw.get("logging", {}),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_config.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/config.py tests/test_config.py
git commit -m "feat: add config loader for agent_config.yaml"
```

---

### Task 3: Add shared test fixtures for LLM and GitHub mocks

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add fixtures to conftest.py**

Append to the existing `tests/conftest.py`:

```python
from unittest.mock import AsyncMock, MagicMock
from agents.llm import LLMClient
from tools.github_tools import GitHubTools, GitHubIssue


@pytest.fixture
def mock_llm_client():
    """Fixture providing a mocked LLM client."""
    client = MagicMock(spec=LLMClient)
    client.generate = AsyncMock(return_value="Mocked LLM response")
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_github_tools():
    """Fixture providing mocked GitHub tools."""
    gh = MagicMock(spec=GitHubTools)
    gh.fetch_recent_issues = AsyncMock(return_value=[
        GitHubIssue(
            number=101,
            title="Bug: SDK init fails on React Native",
            body="Getting crash on startup. I'm switching to Amplitude if this isn't fixed.",
            author="frustrated-dev",
            state="open",
            labels=["bug"],
            created_at="2026-03-10T10:00:00Z",
            updated_at="2026-03-10T10:00:00Z",
            comments_count=3,
            reactions_total=8,
            url="https://github.com/PostHog/posthog/issues/101",
        ),
        GitHubIssue(
            number=102,
            title="Feature Request: Export insights as PDF",
            body="Would be nice to export analytics dashboards.",
            author="happy-user",
            state="open",
            labels=["feature"],
            created_at="2026-03-09T10:00:00Z",
            updated_at="2026-03-09T10:00:00Z",
            comments_count=1,
            reactions_total=15,
            url="https://github.com/PostHog/posthog/issues/102",
        ),
        GitHubIssue(
            number=103,
            title="Docs: Feature flags tutorial is outdated",
            body="The docs reference the old API. Please update.",
            author="docs-reader",
            state="open",
            labels=["documentation"],
            created_at="2026-03-08T10:00:00Z",
            updated_at="2026-03-08T10:00:00Z",
            comments_count=0,
            reactions_total=2,
            url="https://github.com/PostHog/posthog/issues/103",
        ),
    ])
    gh.close = AsyncMock()
    return gh
```

- [ ] **Step 2: Run all existing tests to ensure nothing breaks**

Run: `source .venv/bin/activate && pytest tests/ -v`
Expected: 66+ PASSED (all existing tests still pass)

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "feat: add mock_llm_client and mock_github_tools test fixtures"
```

---

## Chunk 2: Wire Sage's execute()

### Task 4: Wire Sage to GitHubTools + triage_issue()

**Files:**
- Modify: `agents/sage.py:15,118-176` (add GitHubTools import, modify `__init__` and `execute()`)
- Modify: `tests/test_sage.py` (add tests for wired execute)

- [ ] **Step 1: Write the failing test for wired execute()**

Append to `tests/test_sage.py`:

```python
class TestSageExecuteWired:
    """Test that execute() calls GitHubTools and triage_issue()."""

    @pytest.fixture
    def wired_sage(self, posthog_client, knowledge_base_path, mock_github_tools):
        return Sage(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            github_tools=mock_github_tools,
        )

    @pytest.mark.asyncio
    async def test_execute_triages_github_issues(self, wired_sage, mock_github_tools):
        result = await wired_sage.execute("Triage GitHub issues from the past 7 days")
        assert result["status"] == "triaged"
        assert len(result["issues"]) == 3
        mock_github_tools.fetch_recent_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_detects_churn_risk(self, wired_sage):
        result = await wired_sage.execute("Triage issues")
        churn_risks = result["churn_risks"]
        assert "frustrated-dev" in churn_risks

    @pytest.mark.asyncio
    async def test_execute_populates_breakdowns(self, wired_sage):
        result = await wired_sage.execute("Triage issues")
        assert result["sentiment_breakdown"]["churning"] >= 1
        assert result["category_breakdown"]["bug"] >= 1

    @pytest.mark.asyncio
    async def test_execute_without_github_tools_returns_empty(self, posthog_client, knowledge_base_path):
        sage = Sage(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        result = await sage.execute("Triage issues")
        assert result["issues"] == []
        assert result["status"] == "triaged"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_sage.py::TestSageExecuteWired -v`
Expected: FAIL — `Sage()` doesn't accept `github_tools` param yet

- [ ] **Step 3: Modify Sage to accept GitHubTools and wire execute()**

In `agents/sage.py`, make these changes:

1. Add import at top: `from tools.github_tools import GitHubTools, GitHubIssue`
2. Add `github_tools` param to `__init__`:
```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    github_tools: Optional["GitHubTools"] = None,
):
    self.api_client = api_client
    self.knowledge_base_path = knowledge_base_path
    self.github_tools = github_tools
```

3. Replace the stubbed `execute()` body (lines 137–176) with:
```python
async def execute(
    self,
    task: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    logger.info(f"Sage executing: {task[:80]}...")

    # Fetch issues from GitHub (graceful fallback if no tools)
    raw_issues: list[GitHubIssue] = []
    if self.github_tools:
        try:
            raw_issues = await self.github_tools.fetch_recent_issues(days=7)
            # Filter out PRs
            raw_issues = [i for i in raw_issues if not i.is_pull_request]
        except Exception as exc:
            logger.warning(f"GitHub fetch failed: {exc}")

    # Triage each issue
    triaged: list[TriagedIssue] = []
    for issue in raw_issues:
        triaged.append(await self.triage_issue(
            issue_number=issue.number,
            title=issue.title,
            body=issue.body,
            author=issue.author,
        ))

    # Build breakdowns
    sentiment_breakdown: dict[str, int] = {}
    category_breakdown: dict[str, int] = {}
    product_area_breakdown: dict[str, int] = {}
    churn_risks: list[str] = []

    for t in triaged:
        sentiment_breakdown[t.sentiment.value] = sentiment_breakdown.get(t.sentiment.value, 0) + 1
        category_breakdown[t.category] = category_breakdown.get(t.category, 0) + 1
        product_area_breakdown[t.product_area] = product_area_breakdown.get(t.product_area, 0) + 1
        if t.churn_risk:
            churn_risks.append(t.author)

    return {
        "agent": "sage",
        "task": task,
        "issues": [
            {
                "number": t.issue_number,
                "title": t.title,
                "author": t.author,
                "priority": t.priority.value,
                "sentiment": t.sentiment.value,
                "category": t.category,
                "product_area": t.product_area,
                "summary": t.summary,
                "suggested_response": t.suggested_response,
                "churn_risk": t.churn_risk,
            }
            for t in triaged
        ],
        "churn_risks": churn_risks,
        "champions": [],
        "sentiment_breakdown": sentiment_breakdown,
        "category_breakdown": category_breakdown,
        "product_area_breakdown": product_area_breakdown,
        "status": "triaged",
    }
```

- [ ] **Step 4: Run all Sage tests**

Run: `source .venv/bin/activate && pytest tests/test_sage.py -v`
Expected: ALL PASSED (old tests still pass because they use `Sage(api_client=..., knowledge_base_path=...)` without `github_tools`, which defaults to None and returns empty)

- [ ] **Step 5: Commit**

```bash
git add agents/sage.py tests/test_sage.py
git commit -m "feat: wire Sage.execute() to GitHubTools + triage_issue()"
```

---

## Chunk 3: Wire Iris's execute() with LLM theme extraction

### Task 5: Implement LLM-backed theme extraction in Iris

**Files:**
- Modify: `agents/iris.py:8-13,105-157,191-211` (add LLM import, modify `__init__`, `execute()`, `_extract_themes()`, `_map_to_journey()`)
- Modify: `tests/test_iris.py` (add tests for wired execute)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_iris.py`:

```python
import json


class TestIrisExecuteWired:
    """Test that execute() extracts themes via LLM."""

    @pytest.fixture
    def llm_response(self):
        """Sample LLM response for theme extraction."""
        return json.dumps({
            "themes": [
                {
                    "theme_id": "t1",
                    "title": "SDK initialization failures",
                    "description": "Multiple users report SDK crash on startup",
                    "frequency": 3,
                    "severity": 7.0,
                    "sources": ["github"],
                    "representative_quotes": ["Getting crash on startup"],
                    "product_areas": ["sdks"],
                    "recommended_actions": ["Fix React Native SDK init", "Add error boundary docs"],
                    "journey_stage": "onboarding",
                },
                {
                    "theme_id": "t2",
                    "title": "Documentation outdated",
                    "description": "Docs reference old API versions",
                    "frequency": 2,
                    "severity": 4.0,
                    "sources": ["github"],
                    "representative_quotes": ["The docs reference the old API"],
                    "product_areas": ["feature_flags"],
                    "recommended_actions": ["Update feature flags tutorial"],
                    "journey_stage": "evaluation",
                },
            ]
        })

    @pytest.fixture
    def wired_iris(self, posthog_client, knowledge_base_path, mock_llm_client, llm_response):
        mock_llm_client.generate = AsyncMock(return_value=llm_response)
        return Iris(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_extracts_themes(self, wired_iris):
        context = {
            "sage_triage": {
                "issues": [
                    {"number": 101, "title": "Bug: SDK init fails", "category": "bug"},
                    {"number": 103, "title": "Docs outdated", "category": "docs"},
                ],
            },
        }
        result = await wired_iris.execute("Synthesize feedback", context=context)
        assert len(result["themes"]) == 2
        assert result["themes"][0]["title"] == "SDK initialization failures"
        assert result["upstream_issues_processed"] == 2

    @pytest.mark.asyncio
    async def test_execute_maps_journey_from_themes(self, wired_iris):
        context = {"sage_triage": {"issues": [{"number": 1, "title": "test"}]}}
        result = await wired_iris.execute("Synthesize", context=context)
        journey = result["journey_map"]
        # onboarding should have pain points from theme t1
        assert any(
            stage["friction_score"] > 0
            for stage in journey.values()
            if isinstance(stage, dict)
        )

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_empty_themes(self, posthog_client, knowledge_base_path):
        iris = Iris(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        result = await iris.execute("Synthesize", context={"sage_triage": {"issues": [{"number": 1}]}})
        assert result["themes"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_iris.py::TestIrisExecuteWired -v`
Expected: FAIL — `Iris()` doesn't accept `llm_client` yet

- [ ] **Step 3: Modify Iris to accept LLMClient and wire execute()**

In `agents/iris.py`:

1. Add imports:
```python
import json
from agents.llm import LLMClient
```

2. Modify `__init__` to accept optional `llm_client`:
```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
):
    self.api_client = api_client
    self.knowledge_base_path = knowledge_base_path
    self.llm_client = llm_client
```

3. Replace `execute()` body (lines 123–157):
```python
async def execute(
    self,
    task: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    logger.info(f"Iris executing: {task[:80]}...")

    sage_issues = []
    if context and "sage_triage" in context:
        sage_data = context["sage_triage"]
        if isinstance(sage_data, dict):
            sage_issues = sage_data.get("issues", [])

    themes = await self._extract_themes(sage_issues)
    journey_map = self._map_to_journey(themes)
    recommendations = self._generate_recommendations(themes)
    content_gaps = self._find_content_opportunities(themes)

    return {
        "agent": "iris",
        "task": task,
        "themes": [
            {
                "theme_id": t.theme_id,
                "title": t.title,
                "description": t.description,
                "frequency": t.frequency,
                "severity": t.severity,
                "composite_score": t.composite_score,
                "sources": t.sources,
                "product_areas": t.product_areas,
                "recommended_actions": t.recommended_actions,
            }
            for t in themes
        ],
        "journey_map": {
            stage.stage: {
                "friction_score": stage.friction_score,
                "pain_points": stage.pain_points,
                "drop_off_risk": stage.drop_off_risk,
            }
            for stage in journey_map
        },
        "product_recommendations": recommendations,
        "content_opportunities": content_gaps,
        "upstream_issues_processed": len(sage_issues),
        "status": "synthesized",
    }
```

4. Replace `_extract_themes()` (lines 191–196):
```python
async def _extract_themes(
    self, signals: list[dict]
) -> list[FeedbackTheme]:
    """Extract recurring themes from all feedback signals via LLM."""
    if not signals or not self.llm_client:
        return []

    issues_text = "\n".join(
        f"- #{s.get('number', '?')}: {s.get('title', '')} — {s.get('category', 'unknown')}"
        for s in signals
    )

    prompt = f"""Analyze these developer feedback signals and extract recurring themes.

Signals:
{issues_text}

Return a JSON object with a "themes" array. Each theme has:
- theme_id: short unique string
- title: concise theme name
- description: 1-2 sentence explanation
- frequency: how many signals relate to this theme (integer)
- severity: 1-10 severity score (float)
- sources: ["github"] (always for now)
- representative_quotes: list of relevant quotes from the signals
- product_areas: which PostHog areas are affected
- recommended_actions: 1-2 concrete actions to address this
- journey_stage: which developer journey stage this maps to (discovery/evaluation/onboarding/integration/scaling)

Return ONLY valid JSON, no markdown fences."""

    try:
        raw = await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.3,
        )
        data = json.loads(raw)
        themes = []
        for t in data.get("themes", []):
            freq = t.get("frequency", 1)
            sev = t.get("severity", 5.0)
            themes.append(FeedbackTheme(
                theme_id=t.get("theme_id", ""),
                title=t.get("title", ""),
                description=t.get("description", ""),
                frequency=freq,
                severity=sev,
                composite_score=freq * sev,
                sources=t.get("sources", []),
                representative_quotes=t.get("representative_quotes", []),
                product_areas=t.get("product_areas", []),
                recommended_actions=t.get("recommended_actions", []),
            ))
        return sorted(themes, key=lambda t: t.composite_score, reverse=True)
    except Exception as exc:
        logger.warning(f"Theme extraction failed: {exc}")
        return []
```

5. Replace `_map_to_journey()` (lines 198–211) to use theme data:
```python
JOURNEY_KEYWORDS: dict[str, list[str]] = {
    "discovery": ["comparison", "alternative", "vs", "evaluate"],
    "evaluation": ["docs", "documentation", "tutorial", "example", "trial"],
    "onboarding": ["install", "setup", "init", "sdk", "first event", "getting started"],
    "integration": ["ci/cd", "feature flag", "session replay", "warehouse", "pipeline"],
    "scaling": ["scale", "performance", "self-host", "team", "enterprise"],
}

def _map_to_journey(
    self, themes: list[FeedbackTheme]
) -> list[DeveloperJourneyStage]:
    """Map themes to developer journey stages based on product areas and keywords."""
    stage_data: dict[str, list[FeedbackTheme]] = {
        stage: [] for stage in self.JOURNEY_KEYWORDS
    }

    for theme in themes:
        text = f"{theme.title} {theme.description} {' '.join(theme.product_areas)}".lower()
        matched = False
        for stage, keywords in self.JOURNEY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                stage_data[stage].append(theme)
                matched = True
                break
        if not matched:
            stage_data["onboarding"].append(theme)  # default

    result = []
    for stage, matched_themes in stage_data.items():
        if matched_themes:
            avg_severity = sum(t.severity for t in matched_themes) / len(matched_themes)
            risk = "high" if avg_severity >= 7 else "medium" if avg_severity >= 4 else "low"
        else:
            avg_severity = 0.0
            risk = "low"

        result.append(DeveloperJourneyStage(
            stage=stage,
            pain_points=[t.title for t in matched_themes],
            friction_score=round(avg_severity, 1),
            drop_off_risk=risk,
        ))

    return result
```

- [ ] **Step 4: Run all Iris tests**

Run: `source .venv/bin/activate && pytest tests/test_iris.py -v`
Expected: ALL PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/iris.py tests/test_iris.py
git commit -m "feat: wire Iris.execute() to LLM-backed theme extraction"
```

---

## Chunk 4: Wire Nova and Kai

### Task 6: Wire Nova's execute() to its statistical methods

**Files:**
- Modify: `agents/nova.py:130-158` (replace stubbed execute)
- Modify: `tests/test_nova.py` (add tests for wired execute)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nova.py`:

```python
from dataclasses import asdict


class TestNovaExecuteWired:
    """Test that execute() uses upstream themes to design experiments."""

    @pytest.mark.asyncio
    async def test_execute_designs_experiments_from_themes(self, nova):
        context = {
            "iris_themes": {
                "themes": [
                    {
                        "title": "SDK init failures",
                        "severity": 7.0,
                        "product_areas": ["sdks"],
                        "recommended_actions": ["Fix React Native init"],
                    },
                    {
                        "title": "Outdated docs",
                        "severity": 4.0,
                        "product_areas": ["feature_flags"],
                        "recommended_actions": ["Update tutorial"],
                    },
                ],
            },
        }
        result = await nova.execute("Design experiments", context=context)
        assert result["status"] == "designed"
        assert len(result["experiments"]) >= 1
        assert result["experiments"][0]["sample_size_per_arm"] > 0
        assert result["upstream_themes_used"] == 2

    @pytest.mark.asyncio
    async def test_execute_includes_funnel_analysis(self, nova):
        context = {"iris_themes": {"themes": [{"title": "Test", "severity": 5.0, "product_areas": ["analytics"], "recommended_actions": ["Fix"]}]}}
        result = await nova.execute("Design experiments", context=context)
        assert result["funnel_analysis"] is not None
        assert result["funnel_analysis"]["biggest_drop_off_stage"] is not None

    @pytest.mark.asyncio
    async def test_execute_without_themes_returns_empty(self, nova):
        result = await nova.execute("Design experiments", context={})
        assert result["experiments"] == []
        assert result["upstream_themes_used"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_nova.py::TestNovaExecuteWired -v`
Expected: FAIL — current execute() returns empty experiments

- [ ] **Step 3: Replace Nova's execute() body**

In `agents/nova.py`, replace `execute()` (lines 130–158):

```python
async def execute(
    self,
    task: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    logger.info(f"Nova executing: {task[:80]}...")

    themes = []
    if context and "iris_themes" in context:
        iris_data = context["iris_themes"]
        if isinstance(iris_data, dict):
            themes = iris_data.get("themes", [])

    # Design experiments for top themes
    experiments = []
    for theme in themes[:3]:
        title = theme.get("title", "Unknown")
        severity = theme.get("severity", 5.0)
        areas = theme.get("product_areas", ["general"])
        mde = 0.03 if severity >= 7 else 0.05
        baseline = 0.15

        exp = await self.design_experiment(
            hypothesis=f"Addressing '{title}' will improve activation",
            primary_metric=f"{areas[0]}_activation_rate",
            baseline_rate=baseline,
            minimum_detectable_effect=mde,
        )
        experiments.append({
            "experiment_id": exp.experiment_id,
            "hypothesis": exp.hypothesis,
            "primary_metric": exp.primary_metric,
            "sample_size_per_arm": exp.sample_size_per_arm,
            "expected_duration_days": exp.expected_duration_days,
            "success_criteria": exp.success_criteria,
            "guardrail_metrics": exp.guardrail_metrics,
        })

    # Analyze the standard PostHog activation funnel
    funnel_result = None
    if themes:
        funnel = await self.analyze_funnel(
            funnel_name="posthog_activation",
            stages=[
                {"name": "signup", "count": 1000},
                {"name": "sdk_installed", "count": 700},
                {"name": "first_event", "count": 595},
                {"name": "first_insight", "count": 298},
                {"name": "feature_flag_created", "count": 89},
                {"name": "team_invited", "count": 36},
            ],
        )
        funnel_result = {
            "funnel_name": funnel.funnel_name,
            "overall_conversion": funnel.overall_conversion,
            "biggest_drop_off_stage": funnel.biggest_drop_off_stage,
            "recommended_interventions": funnel.recommended_interventions,
        }

    return {
        "agent": "nova",
        "task": task,
        "experiments": experiments,
        "funnel_analysis": funnel_result,
        "cohort_segments": [],
        "upstream_themes_used": len(themes),
        "status": "designed",
    }
```

- [ ] **Step 4: Run all Nova tests**

Run: `source .venv/bin/activate && pytest tests/test_nova.py -v`
Expected: ALL PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/nova.py tests/test_nova.py
git commit -m "feat: wire Nova.execute() to design_experiment() and analyze_funnel()"
```

---

### Task 7: Wire Kai's execute() to LLM content generation

**Files:**
- Modify: `agents/kai.py:8-13,66-172` (add LLM import, modify `__init__`, `execute()`)
- Create: `tests/test_kai.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kai.py
"""Tests for Kai content creator module."""

import pytest
from unittest.mock import AsyncMock
from agents.kai import Kai, ContentPiece


@pytest.fixture
def kai(posthog_client, knowledge_base_path):
    return Kai(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestKaiKnowledgeBase:
    """Test knowledge base search."""

    def test_search_finds_matching_docs(self, kai):
        results = kai.search_knowledge_base("python sdk")
        assert len(results) >= 1
        assert "python" in results[0]["source"].lower()

    def test_search_no_results(self, kai):
        results = kai.search_knowledge_base("nonexistent topic xyz")
        assert results == []


class TestKaiExecuteWired:
    """Test that execute() generates content via LLM."""

    @pytest.fixture
    def wired_kai(self, posthog_client, knowledge_base_path, mock_llm_client):
        mock_llm_client.generate = AsyncMock(return_value=(
            "# Getting Started with PostHog Feature Flags\n\n"
            "This tutorial walks you through setting up feature flags...\n\n"
            "## Prerequisites\n- PostHog account\n- JavaScript SDK installed\n\n"
            "## Step 1: Create a flag\n```javascript\nposthog.isFeatureEnabled('new-ui')\n```\n"
        ))
        return Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_generates_content(self, wired_kai, mock_llm_client):
        result = await wired_kai.execute("Write a tutorial on feature flags")
        assert result["status"] == "generated"
        assert "content" in result
        assert len(result["content"]) > 100
        mock_llm_client.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_includes_grounding_sources(self, wired_kai):
        result = await wired_kai.execute("Write about analytics tracking")
        assert "grounding_sources" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_prompt(self, kai):
        result = await kai.execute("Write a tutorial")
        assert result["status"] == "generated"
        assert "content" not in result
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_uses_upstream_themes(self, wired_kai):
        context = {
            "iris_themes": {
                "themes": [
                    {"title": "SDK init pain", "severity": 7.0},
                ],
            },
        }
        result = await wired_kai.execute("Write tutorial", context=context)
        assert len(result.get("pain_points_addressed", [])) >= 1


class TestKaiWriteTutorial:
    """Test write_tutorial() convenience method."""

    @pytest.mark.asyncio
    async def test_write_tutorial_returns_content_piece(self, kai):
        result = await kai.write_tutorial("Setting up PostHog")
        assert isinstance(result, ContentPiece)
        assert result.content_type == "tutorial"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_kai.py::TestKaiExecuteWired -v`
Expected: FAIL — `Kai()` doesn't accept `llm_client`

- [ ] **Step 3: Modify Kai to accept LLMClient and wire execute()**

In `agents/kai.py`:

1. Add import: `from agents.llm import LLMClient`
2. Modify `__init__`:
```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
):
    self.api_client = api_client
    self.knowledge_base_path = knowledge_base_path
    self.llm_client = llm_client
    self._kb_index = self._build_kb_index()
```

3. Replace `execute()` body (lines 121–172):
```python
async def execute(
    self,
    task: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    logger.info(f"Kai executing: {task[:80]}...")

    grounding_docs = self.search_knowledge_base(task)
    grounding_context = "\n\n".join(
        f"[Source: {doc['source']}]\n{doc['content']}"
        for doc in grounding_docs
    )

    pain_points = []
    if context and "iris_themes" in context:
        themes = context["iris_themes"]
        if isinstance(themes, dict):
            pain_points = [t.get("title", "") for t in themes.get("themes", []) if isinstance(t, dict)]

    prompt = f"""Task: {task}

## Knowledge Base Context
{grounding_context if grounding_context else "No specific docs found — use general PostHog knowledge."}

## Community Context
Top developer pain points from this week:
{chr(10).join(f"- {p}" for p in pain_points[:5]) if pain_points else "No upstream context available."}

## Instructions
Write the content following these guidelines:
- Include working code examples with proper syntax highlighting
- Reference specific PostHog features, APIs, and SDKs
- Structure with clear headings (H2 for sections, H3 for subsections)
- Include prerequisites at the top
- End with next steps and links to relevant docs
- Cite which knowledge base documents you referenced
"""

    base_result = {
        "agent": "kai",
        "task": task,
        "grounding_sources": [doc["source"] for doc in grounding_docs],
        "pain_points_addressed": pain_points[:3],
        "status": "generated",
    }

    if self.llm_client:
        try:
            content = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.7,
            )
            base_result["content"] = content
        except Exception as exc:
            logger.warning(f"Content generation failed: {exc}")
            base_result["prompt_used"] = prompt[:500]
    else:
        base_result["prompt_used"] = prompt[:500]

    return base_result
```

- [ ] **Step 4: Run all Kai tests**

Run: `source .venv/bin/activate && pytest tests/test_kai.py -v`
Expected: ALL PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/kai.py tests/test_kai.py
git commit -m "feat: wire Kai.execute() to LLM content generation"
```

---

## Chunk 5: Wire Atlas to pass dependencies + end-to-end verification

### Task 8: Update Atlas to accept and pass new dependencies

**Files:**
- Modify: `agents/atlas.py:7-20,81-117` (add imports, modify `__init__`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_atlas.py`:

```python
class TestAtlasWithDependencies:
    """Test Atlas passes LLM and GitHub tools to agents."""

    @pytest.mark.asyncio
    async def test_atlas_with_full_dependencies(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            github_tools=mock_github_tools,
        )
        assert atlas.sage.github_tools is mock_github_tools
        assert atlas.iris.llm_client is mock_llm_client
        assert atlas.kai.llm_client is mock_llm_client

    @pytest.mark.asyncio
    async def test_weekly_cycle_with_dependencies(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_github_tools, tmp_path
    ):
        import json
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "themes": [
                {
                    "theme_id": "t1",
                    "title": "SDK crashes",
                    "description": "SDK init fails",
                    "frequency": 2,
                    "severity": 7.0,
                    "sources": ["github"],
                    "representative_quotes": ["crash"],
                    "product_areas": ["sdks"],
                    "recommended_actions": ["Fix init"],
                    "journey_stage": "onboarding",
                }
            ]
        }))
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            github_tools=mock_github_tools,
            archive_dir=tmp_path / "archive",
        )
        context = await atlas.run_weekly_cycle()
        # Sage should have triaged issues
        assert len(context.sage_triage.get("issues", [])) == 3
        # OKRs should reflect real data
        assert context.okr_progress["issues_triaged"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_atlas.py::TestAtlasWithDependencies -v`
Expected: FAIL — `Atlas()` doesn't accept `llm_client` or `github_tools`

- [ ] **Step 3: Modify Atlas to accept and distribute new dependencies**

In `agents/atlas.py`:

1. Add imports:
```python
from agents.llm import LLMClient
from tools.github_tools import GitHubTools
from agents.config import load_config, AgentConfig
```

2. Modify `__init__`:
```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    archive_dir: Path = Path("context_archive"),
    llm_client: Optional[LLMClient] = None,
    github_tools: Optional[GitHubTools] = None,
    config: Optional[AgentConfig] = None,
):
    self.api_client = api_client
    self.knowledge_base_path = knowledge_base_path
    self.archive_dir = archive_dir
    self.llm_client = llm_client
    self.config = config or AgentConfig()
    self.context = SharedContext(
        week_of=datetime.now().strftime("%Y-W%U")
    )

    # Apply config retry settings
    self.MAX_RETRIES = self.config.retry_settings.get("max_retries", 2)
    self.BASE_DELAY = self.config.retry_settings.get("initial_delay_seconds", 2.0)

    # Initialize specialist agents with shared deps
    self.kai = Kai(
        api_client=api_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=llm_client,
    )
    self.sage = Sage(
        api_client=api_client,
        knowledge_base_path=knowledge_base_path,
        github_tools=github_tools,
    )
    self.iris = Iris(
        api_client=api_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=llm_client,
    )
    self.nova = Nova(
        api_client=api_client,
        knowledge_base_path=knowledge_base_path,
    )

    self._agents = {
        "kai": self.kai,
        "sage": self.sage,
        "iris": self.iris,
        "nova": self.nova,
    }
```

3. Update the CLI `main()` to load config and create clients:
```python
async def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Atlas Orchestrator Agent")
    parser.add_argument("--weekly-cycle", action="store_true")
    parser.add_argument("--agent", type=str)
    parser.add_argument("--task", type=str)
    parser.add_argument("--config", type=str, default="config/agent_config.yaml")
    args = parser.parse_args()

    config = load_config(Path(args.config))

    client = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )
    kb_path = Path(__file__).parent.parent / "knowledge_base"

    llm_client = LLMClient(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    ) if os.environ.get("ANTHROPIC_API_KEY") else None

    github_tools = GitHubTools(
        token=os.environ.get("GITHUB_TOKEN", ""),
    ) if os.environ.get("GITHUB_TOKEN") else None

    atlas = Atlas(
        api_client=client,
        knowledge_base_path=kb_path,
        llm_client=llm_client,
        github_tools=github_tools,
        config=config,
    )

    try:
        if args.weekly_cycle:
            context = await atlas.run_weekly_cycle()
            print(json.dumps(context.to_dict(), indent=2, default=str))
        elif args.agent and args.task:
            result = await atlas.run_single_task(args.agent, args.task)
            print(json.dumps(result.__dict__, indent=2, default=str))
        else:
            parser.print_help()
    finally:
        if llm_client:
            await llm_client.close()
        if github_tools:
            await github_tools.close()
        await client.close()
```

- [ ] **Step 4: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v`
Expected: ALL PASSED (80+ tests)

- [ ] **Step 5: Commit**

```bash
git add agents/atlas.py tests/test_atlas.py
git commit -m "feat: wire Atlas to pass LLM + GitHub deps to agents, load config"
```

---

### Task 9: Update CLAUDE.md Known Issues

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Known Issues section**

Replace the Known Issues section in CLAUDE.md to reflect current state:

```markdown
## Known Issues / TODOs

- Knowledge base files are stubs with representative content, not full PostHog docs
- MCP server uses raw stdio JSON-RPC — could upgrade to the official `mcp` Python SDK when stable
- Agent LLM calls require ANTHROPIC_API_KEY env var — system degrades gracefully without it
- GitHub integration requires GITHUB_TOKEN — Sage returns empty results without it
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update Known Issues to reflect wired agent state"
```

---

### Task 10: Run full suite and push

- [ ] **Step 1: Run the complete test suite**

Run: `source .venv/bin/activate && pytest tests/ -v --tb=short`
Expected: 80+ PASSED, 0 FAILED

- [ ] **Step 2: Run linting**

Run: `source .venv/bin/activate && ruff check . && black --check .`
Expected: No errors (fix any that appear)

- [ ] **Step 3: Push to GitHub**

```bash
git push origin main
```
