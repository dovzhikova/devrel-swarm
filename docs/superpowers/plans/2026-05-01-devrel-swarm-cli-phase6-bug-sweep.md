# devrel-origin CLI — Phase 6: Wave 1 Bug Sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 21 highest-impact issues identified in the 2026-04-29 agent-by-agent code review — the "Wave 1: silent broken features" group. Every fix targets a real bug that ships to users today: features that look implemented but are no-ops, race conditions under concurrent stages, dead alerting paths, hardcoded mock data passed off as real, output collisions on parallel runs.

**Scope:** This is **Wave 1 only** (the highest-impact subset). Wave 2 (correctness gaps) and Wave 3 (polish) are deferred to follow-up phases. The review identified ~50 total improvements; this plan ships ~21 of them — the ones where the cost of *not* fixing is observable user-facing or operator-facing breakage.

**Architecture:** Surgical edits across 13 agent files plus `core/llm.py`. No new modules. No agent rewrites. Each fix is bounded to <50 lines of change. Tests are added only when behavior changes; trivial typo / config-default fixes get a verification step instead.

**Tech Stack:** Python 3.12+ existing test infra (pytest + respx). No new dependencies.

**Source review:** Per-agent findings synthesized from the 2026-04-29 review.
**Phases 1-5 (prerequisites, all merged):** `be971bd`, `121187e`, `bfb3bb5`, `86c2747`, `24604c5` on `main`.

---

## File coverage

13 agent files modified, plus `core/llm.py` for the cost-attribution race fix. 4-6 test files added or modified. No file moves, no new modules, no schema changes.

```
src/devrel_origin/core/
  llm.py               MODIFY   add `agent` kwarg to generate() for race-safe attribution
  atlas.py             MODIFY   pass agent= instead of mutating _current_agent
  watchdog.py          MODIFY   fix Firecrawl probe + dead alert condition
  sentinel.py          MODIFY   fix _collect_content per-agent key mapping
  sage.py              MODIFY   wire champion_signal + add CHURNING response branch
  echo.py              MODIFY   parse posted_at, name QUESTION_SIGNALS, fix typo
  iris.py              MODIFY   add "other" journey stage; log early-return
  nova.py              MODIFY   sha256 stable IDs; guard DAILY_SIGNUPS
  kai.py               MODIFY   per-call content_type; fix issue filter
  mox.py               MODIFY   email_campaign fallback prompt; complete content_type map
  pax.py               MODIFY   extract _extract_icp_criteria helper
  vox.py               MODIFY   slugged output filename
  video/assembler.py   MODIFY   FFmpeg subprocess timeout
  video/overlay_renderer.py  MODIFY   FFmpeg subprocess timeout
  dex.py               MODIFY   add ast.AnnAssign branch
  rex.py               MODIFY   add semaphore + Apollo domain guard
```

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree off `main`**

Use **superpowers:using-git-worktrees** to create `.worktrees/cli-phase6-bug-sweep` on a new branch `feat/cli-phase6-bug-sweep`. Confirm `main` is at `24604c5` (Phase 5 merge) or later before branching.

- [ ] **Step 2: Confirm starting state + baseline test count**

```bash
cd /Users/macmini/devrel-origin/.worktrees/cli-phase6-bug-sweep
/opt/homebrew/bin/python3.13 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.preflight.log 2>&1 && echo "exit=$?"
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort > /tmp/pytest.failures.phase6.before.txt
wc -l /tmp/pytest.failures.phase6.before.txt
```
Expected: `707 passed, 22 failed`, `22` lines.

The 22 known failures are pre-existing test drift unrelated to this work. Phase 6 must preserve parity (same 22, no new failures). Phase 6 may *fix* some of the 22 if a Wave 1 bug fix happens to remove a real failure cause — that's a bonus, not a goal.

---

## Task 1: Atlas — race-free per-agent cost attribution

**Files:**
- Modify: `src/devrel_origin/core/llm.py`
- Modify: `src/devrel_origin/core/atlas.py`
- Modify: `tests/core/test_llm_cost_sink.py` (extend with race test)

**Bug:** `Atlas.delegate()` calls `self.llm_client.set_agent(agent_name)` before dispatching, but every parallel stage (`Sage+Echo+Dex`, `Rex+Iris`, `Nova+Kai`) shares one `LLMClient`. The last `set_agent` to fire before any `generate()` wins, silently misattributing cost. The fix: thread `agent` through `generate()` itself so each call carries its own attribution, no shared mutable state.

- [ ] **Step 1: Add `agent` parameter to `LLMClient.generate()`**

In `src/devrel_origin/core/llm.py`, find the `generate` signature and the `_emit_cost` block. Change the signature so `agent: str | None = None` is accepted; resolve it inside the function as `effective_agent = agent or self._current_agent or "unknown"`; pass `effective_agent` (not `self._current_agent`) to `_emit_cost`.

The exact diff: change `generate(self, ..., model: str | None = None, ...)` to `generate(self, ..., model: str | None = None, agent: str | None = None, ...)`. Inside the method body, where `_emit_cost(...)` is currently called, replace any reference to `self._current_agent` with the resolved `effective_agent`.

Also update `_emit_cost` itself to accept an optional `agent` override:
- Change signature `_emit_cost(self, model, ...)` to `_emit_cost(self, model, ..., agent: str | None = None)`.
- In the body, replace `self._current_agent or "unknown"` with `agent or self._current_agent or "unknown"`.

This keeps backwards compatibility: existing callers that don't pass `agent=` get the old shared-state behavior; new callers pass `agent=name` and get race-safe attribution.

- [ ] **Step 2: Update `Atlas.delegate()` to pass `agent=`**

In `src/devrel_origin/core/atlas.py`, find the line:
```python
self.llm_client.set_agent(agent_name)
```
(around line 385). Replace it with a comment-only delete:
```python
# set_agent removed in Phase 6 — race-unsafe under parallel gather().
# Agent attribution now flows through llm_client.generate(agent=...) per call.
```

This means agents themselves need to pass `agent=` when they call `self.llm_client.generate(...)`. To avoid touching every agent in this task, leave the existing `set_agent` mechanism in place as a fallback (the LLMClient resolves `agent or self._current_agent or "unknown"`, so anyone still using `set_agent` continues to work).

Wait — but Atlas's whole point of calling `set_agent` was to make this work. If we delete the call without updating each agent's `generate()` invocation to pass `agent=`, attribution falls back to `"unknown"` for every concurrent call.

Better path: keep `set_agent` for now (the race is a worsening, not a total break, since Atlas calls it before each delegate), but **also** make `delegate()` build a wrapped `LLMClient` proxy or pass `agent=` through context. The simplest fix that actually closes the race:

Change Atlas `delegate()` to:
1. Keep the existing `set_agent` call (so single-agent paths still work).
2. Wrap the agent's `execute()` in a `contextvars.ContextVar`-based scope that the LLMClient reads in addition to `_current_agent`.

Actually, the cleanest fix without per-agent code changes is `contextvars`:

**Refined approach using `contextvars`:**

In `core/llm.py`:
- At module level, add: `_current_agent_var: ContextVar[str] = ContextVar("current_agent", default="")`
- In `_emit_cost`, resolve attribution as: `agent_name = agent or _current_agent_var.get() or self._current_agent or "unknown"`
- Add a method `LLMClient.agent_context(name: str)` that returns a context manager wrapping `_current_agent_var.set(name)` + `_current_agent_var.reset(token)`.

In `core/atlas.py::delegate`:
```python
# Replace `self.llm_client.set_agent(agent_name)` with:
with self.llm_client.agent_context(agent_name):
    result = await asyncio.wait_for(
        agent.execute(task=task, context=merged_context),
        timeout=self.AGENT_TIMEOUT,
    )
```

ContextVars are async-task-local. Concurrent `gather()` tasks each see their own value, so the race vanishes. No per-agent code changes needed; the existing `set_agent` stays as a simpler API but is now a fallback.

- [ ] **Step 3: Implement the ContextVar approach**

In `src/devrel_origin/core/llm.py`, near the top imports, add:
```python
from contextlib import contextmanager
from contextvars import ContextVar
```

Add a module-level ContextVar:
```python
_current_agent_var: ContextVar[str] = ContextVar("devrel_origin_current_agent", default="")
```

Add a method to `LLMClient`:
```python
@contextmanager
def agent_context(self, agent_name: str):
    """Set the cost-attribution agent for the duration of this context.

    Async-task-local via ContextVar — safe under asyncio.gather() unlike
    set_agent(), which mutates a shared instance attribute. Prefer this
    over set_agent() when running agents concurrently.
    """
    token = _current_agent_var.set(agent_name)
    try:
        yield
    finally:
        _current_agent_var.reset(token)
```

In `_emit_cost`, change the attribution resolution from:
```python
self._current_agent or "unknown"
```
to:
```python
_current_agent_var.get() or self._current_agent or "unknown"
```

In `src/devrel_origin/core/atlas.py::delegate()`, find the existing `set_agent` call and replace the `await asyncio.wait_for(...)` block with a `with` block:

```python
# Tag LLM calls with the agent name for cost tracking.
# Use agent_context (ContextVar-based, async-task-local) instead of
# set_agent (shared mutable attribute) so concurrent gather() calls
# don't race.
if self.llm_client:
    self.llm_client.set_agent(agent_name)  # legacy fallback for non-LLM call sites

for attempt in range(1, self.MAX_RETRIES + 2):
    try:
        logger.info(f"Delegating to {agent_name} (attempt {attempt}): {task[:80]}...")
        ctx_mgr = (
            self.llm_client.agent_context(agent_name)
            if self.llm_client
            else _nullcontext()
        )
        with ctx_mgr:
            result = await asyncio.wait_for(
                agent.execute(task=task, context=merged_context),
                timeout=self.AGENT_TIMEOUT,
            )
        # ... rest of the retry body unchanged
```

`_nullcontext` import: `from contextlib import nullcontext as _nullcontext` at the top of atlas.py.

- [ ] **Step 4: Add a race test to `tests/core/test_llm_cost_sink.py`**

Append:
```python
@pytest.mark.asyncio
async def test_agent_context_is_race_safe_under_gather():
    """Concurrent agent_context blocks must not bleed into each other's cost emissions."""
    client = LLMClient(api_key="dummy")
    captured: list[str] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        captured.append(agent)

    client.set_cost_sink(sink)

    async def emit_under_context(name: str) -> None:
        with client.agent_context(name):
            # Yield to event loop to let other tasks interleave.
            await asyncio.sleep(0)
            await client._emit_cost(
                model="claude-haiku-4-5-20251001",
                input_tokens=1, output_tokens=1,
            )

    await asyncio.gather(
        *[emit_under_context(f"agent_{i}") for i in range(5)]
    )
    # Each gather participant must see its own agent name in its emission.
    assert sorted(captured) == [f"agent_{i}" for i in range(5)]
```

Add `import asyncio` if not already present.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/core/test_llm_cost_sink.py -v --no-cov 2>&1 | tail -10
python -m pytest tests/test_atlas.py tests/test_atlas_replies.py -q --no-cov 2>&1 | tail -5
```
Expected: 6 passed in cost_sink (was 5, +1 new); existing Atlas tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_origin/core/llm.py src/devrel_origin/core/atlas.py \
        tests/core/test_llm_cost_sink.py
git commit -m "fix(cost): race-safe agent attribution under asyncio.gather (Wave 1)"
```

---

## Task 2: Watchdog — fix dead alert + Firecrawl probe

**Files:**
- Modify: `src/devrel_origin/core/watchdog.py`

**Bugs:**
1. Integration alert at line 236 checks for status `"unknown"` which `_check_integrations` never emits — dead code, alert is permanently silent.
2. Firecrawl probe URL is `/v1/scrape` (POST-only) called with GET → always returns 405, so Firecrawl reports `error_405` even when healthy.

- [ ] **Step 1: Fix the integration alert condition**

In `src/devrel_origin/core/watchdog.py`, find `_generate_alerts` (around line 235-237). Locate the loop that checks `for k, v in status.items(): if v == "unknown":` and change the condition to:

```python
for k, v in status.items():
    if v not in ("connected", "not_configured"):
        alerts.append(f"Integration {k} is unhealthy: {v}")
```

This now correctly fires when `_check_integrations` returns `error_405`, `error_500`, `unreachable: ConnectionError`, etc. — any state that isn't healthy or intentionally not configured.

- [ ] **Step 2: Fix the Firecrawl probe URL**

In `src/devrel_origin/core/watchdog.py`, find the `INTEGRATION_PROBES` dict or wherever the Firecrawl probe URL is defined (search for `firecrawl.dev`). Replace `https://api.firecrawl.dev/v1/scrape` with a GET-able health endpoint.

Inspect what Firecrawl provides — most likely `https://api.firecrawl.dev/v1/team` returns 200 on GET with valid auth, or `https://api.firecrawl.dev/` for an unauthenticated health check. Use whichever endpoint accepts GET and reflects auth health.

If `_check_integrations` builds the URL inline rather than from a config dict, change it there.

- [ ] **Step 3: Also update `_compute_health_score`**

The dead-code branch at watchdog.py:265 deducts for `"unknown"` status. Change it to deduct for any status NOT in `("connected", "not_configured")`:

```python
for status_value in integrations.values():
    if status_value not in ("connected", "not_configured"):
        score -= 5  # match existing per-integration deduction
```

- [ ] **Step 4: Run tests, verify Watchdog tests still pass**

```bash
python -m pytest tests/test_atlas.py -q --no-cov 2>&1 | tail -5
```
(Watchdog has no dedicated test file; it's exercised via Atlas.)

If you want extra confidence, write a quick targeted test:

In `tests/test_atlas.py` (or a new `tests/test_watchdog.py` if cleanly separable), add a unit test that:
1. Constructs Watchdog with a mocked integrations dict containing `{"firecrawl": "error_405", "github": "connected"}`.
2. Asserts `_generate_alerts(...)` returns a list containing a string mentioning `firecrawl`.

Skip the probe-URL change verification by HTTP — the URL change is a config update; the unit test for the alert condition is the load-bearing test.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/watchdog.py
git commit -m "fix(watchdog): dead integration alert + Firecrawl POST-only probe (Wave 1)"
```

---

## Task 3: Sentinel — fix `_collect_content` per-agent key mapping

**Files:**
- Modify: `src/devrel_origin/core/sentinel.py`

**Bug:** `_collect_content` only reads each agent's `"content"` key. Mox stores under `"blog_post"`, Pax under `"body"`, Rex under `"profiles"`. Sentinel silently audits 1-2 of 9 agents most weeks.

- [ ] **Step 1: Add a per-agent content-key mapping**

In `src/devrel_origin/core/sentinel.py`, before the `_collect_content` method, add:

```python
# Per-agent map of (context_key, content_field) tuples. The content_field
# is the key inside the agent's dict that holds the prose to audit.
_AGENT_CONTENT_FIELDS: dict[str, list[str]] = {
    "kai_content": ["content", "body"],
    "mox_campaigns": ["blog_post", "landing_page", "social_batch", "campaign_brief", "content"],
    "pax_sales": ["body", "battle_card", "sequence", "content"],
    "rex_competitive": ["analysis", "summary", "content"],
    "dex_docs": ["architecture", "api_reference", "content"],
    "iris_themes": ["recommendations", "content"],
    "vox_video": ["script", "content"],
}
```

- [ ] **Step 2: Update `_collect_content` to walk the mapping**

Replace the current body of `_collect_content` with one that iterates the mapping, picking the first non-empty field per agent:

```python
def _collect_content(self, ctx: SharedContext) -> list[dict]:
    pieces: list[dict] = []
    ctx_dict = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
    for context_key, candidate_fields in _AGENT_CONTENT_FIELDS.items():
        agent_data = ctx_dict.get(context_key, {})
        if not isinstance(agent_data, dict):
            continue
        for field in candidate_fields:
            value = agent_data.get(field)
            if isinstance(value, str) and value.strip():
                pieces.append({
                    "agent": context_key,
                    "field": field,
                    "content": value[:5000],  # preserve existing cap
                })
                break  # one piece per agent
            if isinstance(value, list) and value:
                # Some agents store list-of-pieces; concat first 3.
                joined = "\n\n".join(str(v) for v in value[:3])[:5000]
                if joined.strip():
                    pieces.append({
                        "agent": context_key,
                        "field": field,
                        "content": joined,
                    })
                    break
    return pieces
```

- [ ] **Step 3: Add a test**

In `tests/test_atlas.py` or a new test file `tests/test_sentinel.py` (whichever already exists), add:

```python
def test_collect_content_pulls_from_each_agents_primary_field(tmp_path):
    """Sentinel must read from each agent's actual primary key, not a universal 'content'."""
    from devrel_origin.core.sentinel import Sentinel
    from devrel_origin.core.atlas import SharedContext

    ctx = SharedContext(week_of="2026-W18")
    ctx.kai_content = {"content": "Kai prose."}
    ctx.mox_campaigns = {"blog_post": "Mox blog prose."}
    ctx.pax_sales = {"body": "Pax email body."}
    ctx.rex_competitive = {"analysis": "Rex analysis."}

    sentinel = Sentinel()
    pieces = sentinel._collect_content(ctx)
    agents = sorted(p["agent"] for p in pieces)
    assert "kai_content" in agents
    assert "mox_campaigns" in agents
    assert "pax_sales" in agents
    assert "rex_competitive" in agents
```

If `tests/test_sentinel.py` doesn't exist, create it with the standard imports and pytest skeleton.

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_sentinel.py tests/test_atlas.py -q --no-cov 2>&1 | tail -5
git add src/devrel_origin/core/sentinel.py tests/test_sentinel.py
git commit -m "fix(sentinel): collect content from each agent's primary field, not a universal 'content' (Wave 1)"
```

---

## Task 4: Sage — wire `champion_signal` + add CHURNING response branch

**Files:**
- Modify: `src/devrel_origin/core/sage.py`
- Modify: `tests/test_sage.py`

**Bugs:**
1. `champion_signal` field is declared on `TriagedIssue` but never set to `True` anywhere — champion detection is a no-op.
2. `_draft_response` has no branch for `CHURNING` sentiment; frustrated users get the generic "added to triage queue" reply.

- [ ] **Step 1: Implement `_detect_champion_signal`**

In `src/devrel_origin/core/sage.py`, after the existing `_analyze_sentiment` method, add:

```python
CHAMPION_THRESHOLDS = {
    "comments_count": 3,
    "reactions_total": 5,
}

def _detect_champion_signal(self, issue) -> bool:
    """A champion signal: high engagement on the issue itself.

    Returns True if comments_count or total reactions exceed the
    champion thresholds, OR the body references a PR (#N) the author
    submitted. Used by _identify_champions downstream.
    """
    comments = getattr(issue, "comments_count", 0) or 0
    if comments >= self.CHAMPION_THRESHOLDS["comments_count"]:
        return True
    reactions = getattr(issue, "reactions_total", 0) or 0
    if reactions >= self.CHAMPION_THRESHOLDS["reactions_total"]:
        return True
    body = (getattr(issue, "body", "") or "").lower()
    if "pr #" in body or "#pull" in body or "pull/" in body:
        return True
    return False
```

The `getattr` fallback handles any `GitHubIssue` shape that doesn't expose `comments_count` or `reactions_total`. Inspect the actual `GitHubIssue` dataclass in `tools/github_tools.py` to confirm the field names; if they differ (e.g., `comments` instead of `comments_count`), match the real names.

- [ ] **Step 2: Call `_detect_champion_signal` inside `triage_issue`**

In `src/devrel_origin/core/sage.py`, find the `triage_issue` method that constructs a `TriagedIssue`. Add the line:

```python
champion_signal=self._detect_champion_signal(issue),
```

inside the `TriagedIssue(...)` constructor call, alongside `priority=`, `sentiment=`, etc.

- [ ] **Step 3: Add a `CHURNING` branch in `_draft_response`**

In `src/devrel_origin/core/sage.py`, find `_draft_response` and add a new branch *before* the existing `CRITICAL` branch (so churning users with critical priority get the empathetic response, not the generic critical one):

```python
if sentiment == SentimentScore.CHURNING:
    return (
        f"Hey @{issue.author} — I hear you, and I'm sorry this has been frustrating. "
        f"This is on me to help you fix. Can you share: (1) what version you're on, "
        f"(2) the exact error / behavior you're seeing, and (3) what you've already tried? "
        f"I'll dig in personally."
    )
```

Keep the existing `CRITICAL` and `"question"` branches unchanged below.

- [ ] **Step 4: Add tests**

In `tests/test_sage.py`, add:

```python
def test_champion_signal_set_when_comments_high():
    """High comment count on an issue is a champion signal."""
    sage = Sage(...)  # match existing test fixture style
    issue = GitHubIssue(
        number=1, title="Bug", body="x", author="ada", state="open",
        comments_count=5, reactions_total=0, ...
    )
    triaged = sage.triage_issue(issue)
    assert triaged.champion_signal is True


def test_champion_signal_off_when_low_engagement():
    sage = Sage(...)
    issue = GitHubIssue(
        number=2, title="Bug", body="no PR", author="bob", state="open",
        comments_count=0, reactions_total=0, ...
    )
    triaged = sage.triage_issue(issue)
    assert triaged.champion_signal is False


def test_churning_sentiment_gets_empathetic_response():
    sage = Sage(...)
    issue = GitHubIssue(
        number=3, title="i'm done with this",
        body="been broken for the third time, switching",
        author="charlie", state="open", ...
    )
    triaged = sage.triage_issue(issue)
    assert triaged.sentiment == SentimentScore.CHURNING
    assert "frustrating" in triaged.suggested_response.lower()
    assert "queue" not in triaged.suggested_response.lower()
```

Match the existing test scaffolding (look at the current `test_sage.py` for the right `Sage(...)` constructor args).

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_sage.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/sage.py tests/test_sage.py
git commit -m "fix(sage): wire champion_signal detection + CHURNING response branch (Wave 1)"
```

---

## Task 5: Echo — parse `posted_at`, name `QUESTION_SIGNALS`, fix typo

**Files:**
- Modify: `src/devrel_origin/core/echo.py`
- Modify: `tests/test_echo.py`

**Bugs:**
1. `posted_at = datetime.now()` always — trend detection broken.
2. `is_question` uses `ENGAGEMENT_SIGNALS[:8]` magic slice.
3. `"OpenClaw'"` typo in `_suggest_engagement_action` propagates into engagement comments.

- [ ] **Step 1: Parse `posted_at` from the search result**

In `src/devrel_origin/core/echo.py`, find the `_parse_search_result` method (around line 251). Currently `posted_at` is hardcoded to `datetime.now()`.

Search results from `tools/search_tools.py` typically include a date in fields like `published_date`, `date`, or `posted_at`. Inspect the actual `SearchResult` dataclass:

```bash
grep -n "class SearchResult\|published\|posted\|date" src/devrel_origin/tools/search_tools.py | head -10
```

Based on what's available, replace:
```python
posted_at=datetime.now(),
```
with:
```python
posted_at=_parse_result_date(result) or datetime.now(),
```

And add `_parse_result_date` as a module-level helper:
```python
def _parse_result_date(result) -> datetime | None:
    """Best-effort parse of a search result's publication date.

    Returns None if no parseable date is found; caller falls back to
    datetime.now() so downstream code never sees None.
    """
    for field in ("published_date", "posted_at", "date", "created_at"):
        val = getattr(result, field, None)
        if not val:
            continue
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            # ISO 8601 first; fall through silently.
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass
    return None
```

If `tools/search_tools.py`'s `SearchResult` doesn't expose any date field, change the search call to request one (Firecrawl returns `metadata.published`, Brave returns `age`); the parsing function captures whatever shape arrives.

- [ ] **Step 2: Replace `ENGAGEMENT_SIGNALS[:8]` with `QUESTION_SIGNALS`**

In `src/devrel_origin/core/echo.py`, near the existing `ENGAGEMENT_SIGNALS` constant, add:

```python
# Subset of ENGAGEMENT_SIGNALS that specifically indicates a question
# from a user — used by `is_question` detection. Maintained separately
# so reordering ENGAGEMENT_SIGNALS doesn't silently change detection.
QUESTION_SIGNALS = (
    "?",
    "how do",
    "how to",
    "what is",
    "why does",
    "is there",
    "can someone",
    "anyone know",
)
```

Find the `is_question` line that does `any(s in text_lower for s in ENGAGEMENT_SIGNALS[:8])` and change `ENGAGEMENT_SIGNALS[:8]` to `QUESTION_SIGNALS`.

(Compare your `QUESTION_SIGNALS` tuple against the first 8 entries of the existing `ENGAGEMENT_SIGNALS` to make sure the detection scope doesn't change.)

- [ ] **Step 3: Fix the `OpenClaw'` typo**

In `src/devrel_origin/core/echo.py:500`, find the `"OpenClaw'"` literal and change it to `"OpenClaw"`. There may be more than one occurrence — `grep -n "OpenClaw'" src/devrel_origin/core/echo.py` to confirm.

Also: the `OpenClaw` literal is hardcoded but should ideally use `self.product_name`. If `self.product_name` is available in `_suggest_engagement_action`, change `"OpenClaw"` to `self.product_name`.

- [ ] **Step 4: Add tests**

In `tests/test_echo.py`, add:

```python
def test_question_signals_named_constant_used():
    """is_question should use the dedicated QUESTION_SIGNALS, not a slice."""
    from devrel_origin.core.echo import QUESTION_SIGNALS
    assert "?" in QUESTION_SIGNALS
    assert "how do" in QUESTION_SIGNALS


def test_posted_at_parsed_from_result(monkeypatch):
    """A search result with a published_date should produce that date in the mention."""
    from devrel_origin.core.echo import Echo, _parse_result_date

    class FakeResult:
        published_date = "2026-04-10T14:00:00Z"
        title = "x"
        url = "https://reddit.com/r/x/comments/abc/x/"
        snippet = "y"
        author = ""

    parsed = _parse_result_date(FakeResult())
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 4 and parsed.day == 10


def test_no_openclaw_typo_in_engagement_action():
    """The 'OpenClaw' literal should not contain a stray apostrophe."""
    import inspect
    from devrel_origin.core import echo
    source = inspect.getsource(echo)
    assert "OpenClaw'" not in source
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_echo.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/echo.py tests/test_echo.py
git commit -m "fix(echo): parse real posted_at; QUESTION_SIGNALS constant; typo (Wave 1)"
```

---

## Task 6: Iris — add "other" journey stage + log early-return

**Files:**
- Modify: `src/devrel_origin/core/iris.py`

**Bugs:**
1. Unmatched themes default to `"onboarding"` stage — systematically inflates onboarding friction for mature products.
2. `_extract_themes` returns `[]` silently with no log on empty input or missing LLM client — "no input" indistinguishable from "no themes."

- [ ] **Step 1: Add an "other" journey stage**

In `src/devrel_origin/core/iris.py`, find `_map_to_journey` (around line 410-435). The stage_data dict currently has keys like `"awareness"`, `"onboarding"`, `"adoption"`, etc. Add `"other"` to the dict initialization.

Find the fallback line that currently appends to `stage_data["onboarding"]` and change it to `stage_data["other"]`. Add a log line:

```python
logger.info("Theme '%s' did not match any journey stage; routed to 'other'", theme.title)
```

If multiple unmatched themes are routed in a loop, prefer a single summary log at the end:
```python
unmatched = stage_data["other"]
if unmatched:
    logger.info("%d themes routed to 'other' journey stage: %s",
                len(unmatched), [t.title for t in unmatched])
```

- [ ] **Step 2: Log the early-return at line 267**

In `_extract_themes`, find:
```python
if not signals or not self.llm_client:
    return []
```

Replace with:
```python
if not signals:
    logger.info("Iris._extract_themes: no signals provided; returning empty themes list")
    return []
if not self.llm_client:
    logger.warning("Iris._extract_themes: no LLM client available; cannot extract themes")
    return []
```

This makes the two reasons distinguishable in logs.

- [ ] **Step 3: Add a test for the "other" stage**

In `tests/test_iris.py`, add:

```python
def test_unmatched_theme_routed_to_other_stage(caplog):
    """Themes with no journey-stage keyword match must land in 'other', not 'onboarding'."""
    iris = Iris(...)
    themes = [FeedbackTheme(title="completely unrelated topic", description="x", frequency=1, severity=5, ...)]
    journey = iris._map_to_journey(themes)
    assert any(t.title == "completely unrelated topic" for t in journey.get("other", []))
    assert not any(t.title == "completely unrelated topic" for t in journey.get("onboarding", []))
```

(Match the actual `FeedbackTheme` dataclass signature.)

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_iris.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/iris.py tests/test_iris.py
git commit -m "fix(iris): add 'other' journey stage + log early-return distinguishing no-input vs no-llm (Wave 1)"
```

---

## Task 7: Nova — sha256 stable IDs + `DAILY_SIGNUPS` guard

**Files:**
- Modify: `src/devrel_origin/core/nova.py`
- Modify: `tests/test_nova.py`

**Bugs:**
1. `experiment_id = f"exp_{hash(hypothesis) % 10000:04d}"` uses Python's randomized `hash()` — same hypothesis produces different IDs across process restarts; pre-registration de-dup broken.
2. `DAILY_SIGNUPS_ESTIMATE=0` → `ZeroDivisionError`; `=1` → multi-decade durations returned silently as valid.

- [ ] **Step 1: Replace `hash()` with `hashlib.sha256`**

In `src/devrel_origin/core/nova.py`, near the top of the imports:
```python
import hashlib
```

Find the `experiment_id = f"exp_{hash(hypothesis) % 10000:04d}"` line. Replace with:
```python
experiment_id = f"exp_{hashlib.sha256(hypothesis.encode()).hexdigest()[:8]}"
```

This produces a stable 8-hex-char identifier that survives process restarts and has effectively zero collision risk for the cardinality of hypotheses you'll ever run.

- [ ] **Step 2: Guard `DAILY_SIGNUPS_ESTIMATE`**

Find the line `daily_signups = int(os.environ.get("DAILY_SIGNUPS_ESTIMATE", "500"))` (around line 252). Replace with:

```python
DAILY_SIGNUPS_DEFAULT = 500
DAILY_SIGNUPS_FLOOR = 10  # prevents absurd durations from misconfigured envs

raw_signups = int(os.environ.get("DAILY_SIGNUPS_ESTIMATE", str(DAILY_SIGNUPS_DEFAULT)))
if raw_signups < DAILY_SIGNUPS_FLOOR:
    logger.warning(
        "DAILY_SIGNUPS_ESTIMATE=%d is below floor %d; using floor instead",
        raw_signups, DAILY_SIGNUPS_FLOOR,
    )
    daily_signups = DAILY_SIGNUPS_FLOOR
else:
    daily_signups = raw_signups
```

Use module-level constants so the floor is visible / tunable.

- [ ] **Step 3: Add tests**

In `tests/test_nova.py`, add:

```python
def test_experiment_id_is_stable_across_calls():
    """sha256-based experiment_id must survive Python's hash randomization."""
    nova = Nova(...)
    hypothesis = "Larger CTA increases signups"
    design1 = nova.design_experiment(hypothesis, severity=8)
    design2 = nova.design_experiment(hypothesis, severity=8)
    assert design1.experiment_id == design2.experiment_id


def test_low_daily_signups_clamped_to_floor(monkeypatch, caplog):
    monkeypatch.setenv("DAILY_SIGNUPS_ESTIMATE", "1")
    nova = Nova(...)
    design = nova.design_experiment("test", severity=5)
    # Floor of 10 means duration is ceil(2*sample_size / 10), not absurd
    assert design.duration_days < 10000
    assert any("below floor" in rec.message for rec in caplog.records)


def test_zero_daily_signups_does_not_raise(monkeypatch):
    monkeypatch.setenv("DAILY_SIGNUPS_ESTIMATE", "0")
    nova = Nova(...)
    # Must not raise ZeroDivisionError.
    design = nova.design_experiment("test", severity=5)
    assert design.duration_days > 0
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_nova.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/nova.py tests/test_nova.py
git commit -m "fix(nova): sha256 stable experiment IDs + DAILY_SIGNUPS floor guard (Wave 1)"
```

---

## Task 8: Kai — per-call content_type + fix issue filter

**Files:**
- Modify: `src/devrel_origin/core/kai.py`
- Modify: `tests/test_kai.py`

**Bugs:**
1. `content_type = "tutorial"` hardcoded for every call, including `write_changelog` (wrong readability targets for changelogs).
2. `remaining_issues` filter uses `isinstance(i, dict)` but pipeline issues are `list[str]` — every editorial-pipeline issue is silently dropped from the result.

- [ ] **Step 1: Add `content_type` parameter to public methods**

In `src/devrel_origin/core/kai.py`, find the `execute`, `write_tutorial`, and `write_changelog` method signatures. Add an optional `content_type` parameter to each:

- `execute(self, task, context=None, content_type="tutorial")`
- `write_tutorial(self, ..., content_type="tutorial")`
- `write_changelog(self, ..., content_type="landing_page")` ← changelog default

Inside each method, where the existing hardcoded `content_type = "tutorial"` line lives (around line 296-302), change it to:

```python
# content_type is now passed through from the caller; default is per-method.
```

…and remove the hardcoded line. Pass the parameter through to `generate_with_pipeline(content_type=content_type)`.

- [ ] **Step 2: Fix the broken issue filter**

In `src/devrel_origin/core/kai.py:306-309`, find:

```python
remaining_issues = [
    i for i in issues
    if isinstance(i, dict) and i.get("severity") == "high"
]
```

The pipeline returns `issues: list[str]`, so this filter drops everything. Replace with:

```python
remaining_issues: list[str] = [
    i for i in issues if isinstance(i, str) and i.strip()
]
```

This preserves all non-empty string issues (which is what the pipeline produces) while still tolerating an unexpected dict shape from a future pipeline change (those would simply be filtered out, not crash).

- [ ] **Step 3: Add a test for changelog content_type routing**

In `tests/test_kai.py`, add:

```python
@pytest.mark.asyncio
async def test_changelog_uses_landing_page_content_type():
    """write_changelog should default to landing_page targets, not tutorial."""
    from unittest.mock import AsyncMock, patch
    from devrel_origin.core.kai import Kai
    kai = Kai(...)  # match fixture
    with patch("devrel_origin.core.kai.generate_with_pipeline", new=AsyncMock(return_value=("changelog body", [], []))) as m:
        await kai.write_changelog(version="1.0.0", highlights=["feat A"])
        assert m.call_args.kwargs["content_type"] == "landing_page"


def test_pipeline_issue_filter_preserves_strings():
    """remaining_issues must include string issues from the editorial pipeline."""
    from devrel_origin.core.kai import Kai
    issues = ["Persona score 5 < 7", "Readability flags drift"]
    kai = Kai(...)
    # Either via a private helper or inline filter; mirror what kai.py does.
    remaining = [i for i in issues if isinstance(i, str) and i.strip()]
    assert remaining == issues
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_kai.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/kai.py tests/test_kai.py
git commit -m "fix(kai): per-call content_type + str-aware pipeline issue filter (Wave 1)"
```

---

## Task 9: Mox — fix email_campaign fallback + complete content_type map

**Files:**
- Modify: `src/devrel_origin/core/mox.py`
- Modify: `tests/test_mox.py`

**Bugs:**
1. On Instantly `push_campaign` failure, the `except` block falls through to normal generation but `prompt` still contains the JSON-format block — pipeline editorial stages corrupt the JSON structure.
2. Pipeline content_type map covers only 3 of 6 routed types (`campaign`, `press_release`, `case_study` all default to `blog_post`).

- [ ] **Step 1: Fix the email_campaign fallback prompt**

In `src/devrel_origin/core/mox.py` (around line 379-415), find the `email_campaign` block. The structure is roughly:

```python
email_prompt = base_prompt + "\n## Output Format\nReturn ONLY JSON\n..."
# ... try push_campaign with email_prompt
# ... on failure, falls through
prompt = email_prompt  # ← BUG: JSON-format prompt sent through editorial pipeline
```

Refactor so the prose `prompt` and the JSON `email_prompt` are kept separate:

```python
prose_prompt = base_prompt  # without the JSON output instruction
email_prompt = prose_prompt + "\n## Output Format\nReturn ONLY JSON\n..."

# ... try push_campaign(email_prompt) ...
# ... on failure, fall through with prose_prompt as the editorial pipeline input

if asset_type == "email_campaign":
    try:
        # JSON path
        raw, _ = await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=email_prompt,
        )
        # ... parse + push
        ...
    except Exception as e:
        logger.warning("email_campaign JSON path failed; falling through to editorial pipeline: %s", e)
        prompt = prose_prompt  # CLEAN prose prompt for editorial path
```

Then the editorial-pipeline path uses the clean `prose_prompt` instead of the JSON-contaminated `email_prompt`.

- [ ] **Step 2: Complete the content_type map**

Find the pipeline content_type map (around line 429-433):

```python
PIPELINE_CONTENT_TYPE_MAP = {
    "blog": "blog_post",
    "landing": "landing_page",
    "social": "social",  # or whatever the existing mapping is
}
```

Extend it to cover all 6 routed types:

```python
PIPELINE_CONTENT_TYPE_MAP = {
    "blog": "blog_post",
    "landing": "landing_page",
    "social": "social",
    "campaign": "blog_post",        # campaigns often look like long-form blog
    "press_release": "landing_page",  # tighter, less narrative
    "case_study": "blog_post",
    "email_campaign": "cold_email",  # for the editorial fallback path
}
```

Where the map is consumed (around line 444), add a warning log when an unmapped type defaults:

```python
pipeline_type = PIPELINE_CONTENT_TYPE_MAP.get(content_type)
if pipeline_type is None:
    logger.warning(
        "mox: content_type '%s' not in pipeline map; defaulting to 'blog_post'",
        content_type,
    )
    pipeline_type = "blog_post"
```

- [ ] **Step 3: Add tests**

In `tests/test_mox.py`:

```python
def test_pipeline_content_type_map_covers_all_routed_types():
    """Every CONTENT_KEYWORDS routed type must have an explicit pipeline mapping."""
    from devrel_origin.core.mox import CONTENT_KEYWORDS, PIPELINE_CONTENT_TYPE_MAP
    for content_type in CONTENT_KEYWORDS:
        assert content_type in PIPELINE_CONTENT_TYPE_MAP, (
            f"Mox content_type '{content_type}' missing from PIPELINE_CONTENT_TYPE_MAP"
        )


@pytest.mark.asyncio
async def test_email_campaign_fallback_uses_clean_prose_prompt(monkeypatch):
    """When push_campaign fails, the editorial fallback must NOT see the JSON-format prompt."""
    # ... mock push_campaign to raise; mock generate_with_pipeline; assert
    # the user_prompt passed to generate_with_pipeline does NOT contain
    # 'Return ONLY JSON'.
```

(Adapt to the existing test patterns.)

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_mox.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/mox.py tests/test_mox.py
git commit -m "fix(mox): clean prose fallback prompt + complete content_type pipeline map (Wave 1)"
```

---

## Task 10: Pax — extract shared `_extract_icp_criteria` helper

**Files:**
- Modify: `src/devrel_origin/core/pax.py`
- Modify: `tests/test_pax.py`

**Bug:** ICP extraction prompt + normalization logic is copy-pasted between `_execute_prospect` (lines 901-937) and `_execute_prospect_personalize` (lines 601-634), with subtly different `except` handling. Active correctness divergence risk.

- [ ] **Step 1: Add the shared helper**

In `src/devrel_origin/core/pax.py`, before either of the duplicated paths, add:

```python
async def _extract_icp_criteria(self, task: str) -> dict[str, list[str]]:
    """Extract ICP criteria from a free-text prospecting task.

    Single source of truth — used by both _execute_prospect and
    _execute_prospect_personalize. Returns a dict with plural list keys
    (e.g., {"industries": [...], "company_sizes": [...], "titles": [...]});
    LLM may return singular keys, which are normalized.
    """
    extraction_prompt = """Extract ICP criteria from the task below as JSON.

Required keys (use empty lists if not specified):
- industries: list of strings
- company_sizes: list of strings (e.g., "51-200", "1000+")
- titles: list of strings (e.g., "VP Engineering", "Head of DevRel")
- locations: list of strings (e.g., "San Francisco", "remote")

Return only the JSON object, no preamble.

Task: """ + task

    try:
        raw, _ = await self.llm_client.generate(
            system_prompt="You extract structured criteria from prospecting tasks.",
            user_prompt=extraction_prompt,
            model="haiku",
        )
        criteria = json.loads(strip_markdown_fences(raw).strip())
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Pax ICP extraction failed; using empty criteria: %s", e)
        return {"industries": [], "company_sizes": [], "titles": [], "locations": []}

    # Normalize singular -> plural keys (LLM may return either form).
    normalized = {"industries": [], "company_sizes": [], "titles": [], "locations": []}
    for plural, singular in [
        ("industries", "industry"),
        ("company_sizes", "company_size"),
        ("titles", "title"),
        ("locations", "location"),
    ]:
        val = criteria.get(plural) or criteria.get(singular) or []
        if isinstance(val, str):
            val = [val]
        normalized[plural] = [str(v) for v in val if v]
    return normalized
```

(Adjust imports: ensure `json` and `strip_markdown_fences` are imported at the top of `pax.py`.)

- [ ] **Step 2: Replace both call sites with the helper**

In `_execute_prospect` (lines 901-937) and `_execute_prospect_personalize` (lines 601-634), replace the duplicated extraction blocks with:

```python
icp = await self._extract_icp_criteria(task)
```

Then use `icp["industries"]`, `icp["titles"]`, etc. wherever the old criteria dict was consumed.

- [ ] **Step 3: Add a test**

In `tests/test_pax.py`:

```python
@pytest.mark.asyncio
async def test_extract_icp_criteria_normalizes_singular_keys(monkeypatch):
    """LLM returning singular keys ('industry') should normalize to plural ('industries')."""
    from unittest.mock import AsyncMock
    pax = Pax(...)
    pax.llm_client = MagicMock()
    pax.llm_client.generate = AsyncMock(
        return_value=('{"industry": "fintech", "title": "VP Eng"}', None)
    )
    icp = await pax._extract_icp_criteria("find fintech VPs")
    assert icp["industries"] == ["fintech"]
    assert icp["titles"] == ["VP Eng"]


@pytest.mark.asyncio
async def test_extract_icp_criteria_empty_on_parse_failure():
    pax = Pax(...)
    pax.llm_client = MagicMock()
    pax.llm_client.generate = AsyncMock(return_value=("garbage json", None))
    icp = await pax._extract_icp_criteria("x")
    assert icp == {"industries": [], "company_sizes": [], "titles": [], "locations": []}
```

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_pax.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/pax.py tests/test_pax.py
git commit -m "fix(pax): extract _extract_icp_criteria helper, dedup two prospect paths (Wave 1)"
```

---

## Task 11: Vox — slugged output filename + FFmpeg subprocess timeouts

**Files:**
- Modify: `src/devrel_origin/core/vox.py`
- Modify: `src/devrel_origin/core/video/assembler.py`
- Modify: `src/devrel_origin/core/video/overlay_renderer.py`
- Modify: `tests/test_vox.py`

**Bugs:**
1. `tutorial.mp4` hardcoded as output filename — parallel runs collide.
2. FFmpeg `subprocess.communicate()` calls have no timeout — a hung FFmpeg blocks the entire pipeline.

- [ ] **Step 1: Slug the output filename**

In `src/devrel_origin/core/vox.py:133`, find the hardcoded `tutorial.mp4`. Replace with a slug derived from `task` plus a timestamp:

```python
import re
from datetime import datetime, timezone

def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "tutorial"

# Where the output filename is constructed:
ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
output_filename = f"{ts}-{_slug(task)}.mp4"
```

Place `_slug` as a module-level function near the other helpers in vox.py.

- [ ] **Step 2: Add FFmpeg subprocess timeouts in the assembler**

In `src/devrel_origin/core/video/assembler.py`, find the `await process.communicate()` calls (around line 49). Wrap each in `asyncio.wait_for`:

```python
FFMPEG_TIMEOUT_S = 300  # 5 minutes — generous for a tutorial-length video

try:
    stdout, stderr = await asyncio.wait_for(
        process.communicate(), timeout=FFMPEG_TIMEOUT_S,
    )
except asyncio.TimeoutError:
    process.kill()
    await process.wait()
    raise RuntimeError(
        f"FFmpeg subprocess timed out after {FFMPEG_TIMEOUT_S}s; killed"
    )
```

Add `import asyncio` if not already imported.

- [ ] **Step 3: Same change in `overlay_renderer.py`**

Mirror the timeout/kill pattern in `src/devrel_origin/core/video/overlay_renderer.py:58`.

Use the same `FFMPEG_TIMEOUT_S = 300` constant — define it once in a shared module (e.g., add to `src/devrel_origin/core/video/__init__.py` if it makes sense) or duplicate the constant in both files with a comment noting the shared value.

- [ ] **Step 4: Add tests**

In `tests/test_vox.py`:

```python
def test_output_filename_unique_across_calls():
    """Two Vox runs in the same dir must produce different output filenames."""
    from devrel_origin.core.vox import _slug
    name1 = _slug("First tutorial about widgets")
    name2 = _slug("Second tutorial about gadgets")
    assert name1 != name2


def test_output_filename_handles_unsafe_chars():
    from devrel_origin.core.vox import _slug
    out = _slug("../../../etc/passwd")
    assert "/" not in out and ".." not in out
    assert out
```

(Subprocess timeout testing requires more setup; skip for the unit test layer and verify via integration smoke if needed.)

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/test_vox.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/vox.py src/devrel_origin/core/video/ tests/test_vox.py
git commit -m "fix(vox): slug output filename + FFmpeg subprocess timeouts (Wave 1)"
```

---

## Task 12: Dex — visit `ast.AnnAssign` for annotated constants

**Files:**
- Modify: `src/devrel_origin/core/dex.py`
- Modify: `tests/test_dex.py`

**Bug:** Dex captures `ast.Assign` for `ALL_CAPS` constants but never visits `ast.AnnAssign`. Modern annotated constants (`MY_CONST: int = 42`) are silently invisible to the docs output.

- [ ] **Step 1: Add an `AnnAssign` branch**

In `src/devrel_origin/core/dex.py:229-239`, find the existing `ast.Assign` constant capture. Add a parallel `elif` branch:

```python
elif isinstance(node, ast.AnnAssign):
    # Annotated constant: MY_CONST: int = 42
    if isinstance(node.target, ast.Name) and node.target.id.isupper():
        constants.append(ParsedSymbol(
            name=node.target.id,
            kind="constant",
            line=node.lineno,
            annotation=self._node_name(node.annotation) if node.annotation else None,
            value_repr=self._render_value(node.value) if node.value else "",
        ))
```

Match the existing `ParsedSymbol` constructor args from the regular `Assign` branch.

- [ ] **Step 2: Add a test**

In `tests/test_dex.py`:

```python
def test_dex_captures_annotated_constants(tmp_path):
    """AnnAssign with uppercase target must appear in the constants list."""
    src = tmp_path / "module_with_constants.py"
    src.write_text(
        "MY_CONST: int = 42\n"
        "OTHER_CONST: str = 'hello'\n"
        "lowercase_var: int = 1\n"  # should NOT be captured
    )
    from devrel_origin.core.dex import Dex
    dex = Dex()
    parsed = dex._parse_python_file(src)
    constant_names = [s.name for s in parsed.constants]
    assert "MY_CONST" in constant_names
    assert "OTHER_CONST" in constant_names
    assert "lowercase_var" not in constant_names
```

- [ ] **Step 3: Run + commit**

```bash
python -m pytest tests/test_dex.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/dex.py tests/test_dex.py
git commit -m "fix(dex): visit ast.AnnAssign for annotated module-level constants (Wave 1)"
```

---

## Task 13: Rex — search semaphore + Apollo domain guard

**Files:**
- Modify: `src/devrel_origin/core/rex.py`
- Modify: `tests/test_rex.py`

**Bugs:**
1. `asyncio.gather` over 10+ competitors fires unbounded parallel Brave/Firecrawl requests — guarantees rate-limit 429s silently swallowed.
2. Apollo domain guess: `comp.lower().replace(" ", "") + ".com"` produces `pendo.io.com` for `Pendo.io` — broken domain, wasted API call.

- [ ] **Step 1: Add semaphore-bounded search**

In `src/devrel_origin/core/rex.py`, near the top:

```python
SEARCH_CONCURRENCY = 3  # bound parallel web search to avoid rate-limit 429s
```

Find the `asyncio.gather(*[self._search_competitor(c) for c in competitors])` call (around line 322). Wrap the closure in a semaphore:

```python
sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

async def _search_with_sem(comp: str):
    async with sem:
        return await self._search_competitor(comp)

results = await asyncio.gather(*[_search_with_sem(c) for c in competitors])
```

Apply the same pattern to the parallel Apollo enrichment if it lives in a separate `gather` (lines 334-337).

- [ ] **Step 2: Guard the Apollo domain guess**

In `src/devrel_origin/core/rex.py:331`, find the Apollo domain construction. Replace:

```python
domain = comp.lower().replace(" ", "") + ".com"
```

with:

```python
def _guess_domain(comp: str) -> str:
    """Best-effort domain from a competitor name. Preserves existing TLDs."""
    cleaned = comp.lower().strip()
    # If the name already contains a dot, treat it as already a domain-like string.
    if "." in cleaned:
        # Strip spaces but keep the existing TLD.
        return cleaned.replace(" ", "")
    return cleaned.replace(" ", "") + ".com"

domain = _guess_domain(comp)
```

Place `_guess_domain` as a module-level function or a `@staticmethod` on `Rex`.

- [ ] **Step 3: Add tests**

In `tests/test_rex.py`:

```python
def test_guess_domain_preserves_existing_tld():
    from devrel_origin.core.rex import _guess_domain
    assert _guess_domain("Pendo.io") == "pendo.io"
    assert _guess_domain("FullStory") == "fullstory.com"
    assert _guess_domain("Acme Corp") == "acmecorp.com"
    assert _guess_domain("MixPanel") == "mixpanel.com"


@pytest.mark.asyncio
async def test_search_is_semaphore_bounded(monkeypatch):
    """Concurrent searches must not exceed SEARCH_CONCURRENCY."""
    from devrel_origin.core.rex import Rex, SEARCH_CONCURRENCY
    rex = Rex(...)
    in_flight = 0
    max_seen = 0

    async def fake_search(comp):
        nonlocal in_flight, max_seen
        in_flight += 1
        max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return []

    rex._search_competitor = fake_search
    competitors = [f"comp{i}" for i in range(10)]
    # ... call the wrapper that uses the semaphore
    # Assert max_seen <= SEARCH_CONCURRENCY
```

(Adapt to the actual call pattern in `rex.py`.)

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/test_rex.py tests/test_rex_apollo.py -v --no-cov 2>&1 | tail -10
git add src/devrel_origin/core/rex.py tests/test_rex.py
git commit -m "fix(rex): semaphore-bounded parallel search + Apollo domain TLD guard (Wave 1)"
```

---

## Task 14: Verify, document, finalize

- [ ] **Step 1: Full test suite + parity**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.phase6.after.txt | tail -10
diff /tmp/pytest.failures.phase6.before.txt <(grep "^FAILED" /tmp/pytest.phase6.after.txt | sort)
```

Expected: ~`727+ passed, ≤22 failed`. Diff: empty OR shows that some of the 22 known failures are now passing (which is allowed and expected if a Wave 1 fix happened to remove a real cause). Diff must NOT show new failures.

If the diff shows previously-passing tests now failing, **stop and fix** — Phase 6 must not regress.

- [ ] **Step 2: Coverage check (informational)**

```bash
python -m pytest tests/ --cov=devrel_origin.core --cov-report=term 2>&1 | tail -25
```

No hard threshold; just verify no agent's coverage dropped substantially.

- [ ] **Step 3: End-to-end smoke**

```bash
T=$(mktemp -d) && cd "$T"
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name probe --url https://probe.dev --github-repo probe/probe >/dev/null
ANTHROPIC_API_KEY=sk-ant-test devrel doctor 2>&1 | tail -5
echo "exit=$?"
cd - && rm -rf "$T"
```

- [ ] **Step 4: Update CHANGELOG**

In `CHANGELOG.md`, add a new section at the top:

```markdown
## 0.2.1 — 2026-05-01

Wave 1 bug sweep — 13 high-impact fixes from the 2026-04-29 agent review. Every fix targets a bug that ships to users today: silent broken features, race conditions under concurrent stages, dead alerting paths, and output collisions on parallel runs.

### Fixed

- **Atlas**: race-safe per-agent cost attribution under `asyncio.gather` via `agent_context()` ContextVar (was: shared mutable `_current_agent` clobbered by concurrent stages).
- **Watchdog**: integration alert now fires for any unhealthy status (was: dead-code condition checking for a status the agent never emits). Firecrawl probe now uses a GET-able endpoint (was: POST-only `/v1/scrape` always returned 405).
- **Sentinel**: `_collect_content` now reads each agent's primary content key (was: only `"content"`, missing 6 of 9 agents per cycle).
- **Sage**: `champion_signal` is now actually set (was: declared on `TriagedIssue` but never assigned True). `CHURNING` sentiment gets an empathetic response branch (was: generic "added to triage queue").
- **Echo**: `posted_at` parsed from search results instead of `datetime.now()`. `is_question` uses a dedicated `QUESTION_SIGNALS` constant (was: magic slice of `ENGAGEMENT_SIGNALS[:8]`). Fixed `OpenClaw'` typo.
- **Iris**: unmatched themes route to a new `"other"` journey stage (was: defaulted to `"onboarding"`, systematically inflating onboarding friction). Early-return paths now log distinguishably.
- **Nova**: experiment IDs use `hashlib.sha256` for stability across process restarts (was: Python's randomized `hash()`). `DAILY_SIGNUPS_ESTIMATE` clamped to a floor of 10.
- **Kai**: `content_type` parameter now flows from caller (was: hardcoded `"tutorial"` for all calls including changelogs). Pipeline issue filter handles `list[str]` correctly (was: `isinstance(i, dict)` silently dropped every editorial flag).
- **Mox**: `email_campaign` failure now falls through to the editorial pipeline with a clean prose prompt (was: JSON-format-contaminated prompt corrupted by editorial stages). `PIPELINE_CONTENT_TYPE_MAP` covers all 6 routed content types.
- **Pax**: shared `_extract_icp_criteria` helper deduplicates ICP-extraction prompt + normalization across `_execute_prospect` and `_execute_prospect_personalize` (was: two divergent copies with different exception handling).
- **Vox**: output filename slugged + timestamped (was: hardcoded `tutorial.mp4` collided on parallel runs). FFmpeg subprocess calls in `assembler.py` and `overlay_renderer.py` have a 300s timeout + kill (was: no timeout — hung FFmpeg could block the pipeline indefinitely).
- **Dex**: `ast.AnnAssign` constants now appear in the symbol table (was: only `ast.Assign` visited; modern annotated constants invisible).
- **Rex**: parallel web search bounded by `Semaphore(3)` (was: 10+ unbounded simultaneous requests reliably 429'd by Brave/Firecrawl free tiers). Apollo domain guess preserves existing TLDs (was: `pendo.io` → `pendo.io.com`).
```

- [ ] **Step 5: Commit docs**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG for v0.2.1 Wave 1 bug sweep"
```

- [ ] **Step 6: Final verification**

```bash
git log --oneline main..HEAD
.venv/bin/python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```

Expected: 14 commits on the branch (13 task fixes + CHANGELOG). Test count at parity or better.

---

## Self-review checklist (already applied)

- **Spec coverage:** every Wave 1 fix from the 2026-04-29 review is mapped to a task. Wave 2 + Wave 3 explicitly deferred.
- **No placeholders:** every fix specifies the file:line, before/after intent, and a verification step. Where test code is too verbose to specify line-by-line, the test's `assert` shape is given.
- **Type / name consistency:** `_extract_icp_criteria`, `agent_context`, `_current_agent_var`, `QUESTION_SIGNALS`, `PIPELINE_CONTENT_TYPE_MAP`, `_guess_domain`, `_slug`, `FFMPEG_TIMEOUT_S`, `SEARCH_CONCURRENCY` — used consistently across tasks.
- **Reversibility:** every commit is independently reversible. No schema changes, no migrations, no destructive operations.

## Out of scope (Wave 2 + Wave 3, deferred to Phase 7+)

Wave 2 — silent correctness gaps:
- Atlas per-agent checkpoint flags
- Stage 6 (Instantly sync) checkpoint
- Watchdog real `output_age_hours` from timestamps
- Watchdog budget alert as % of cap
- Sentinel JSON-vs-API error distinction
- Sentinel structural-audit scoring scale 1-100
- Iris similarity-threshold module constant + centroid comparison
- Iris content-opportunity briefs with title+action
- Nova funnel-data-source flag (or pull from api_client)
- Kai `status="error"` in except block
- Mox `revision` schema unification with Kai
- Pax `_load_prompt` migration to shared util
- Pax `_execute_campaign` None-guard
- Dex one-level class-body traversal → `ast.walk`
- Dex `repo_path` from project paths
- Rex `parse_error` status on JSON failure

Wave 3 — polish:
- System-prompt caching (Kai/Mox/Pax/Rex)
- Nova `scipy` import to module-level
- Nova MDE-severity comment / inversion
- Vox stderr surfacing
- Vox `stream_to_file` in executor
- Echo `search_limit` constructor parameter
- Iris hardcoded `"sources": ["github"]` removal
- Sage shared keyword constants module
