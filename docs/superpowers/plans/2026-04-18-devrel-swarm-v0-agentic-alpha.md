# devrel-swarm v0 Agentic Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the central control app + a Fly-hosted OpenClaw instance, provisioned (manually first, then automated) by the central app. Dashboard + chat + prompt editor all functional against the live instance. Cost tracking accurate to ±5% of Anthropic invoice.

**Architecture:** Each customer gets their own Fly Machine running the full `devrel-swarm` repo. Persistence is SQLite on a persistent volume inside the Machine. An HTTP bridge (FastAPI wrapping the existing MCP server + adding run/deliverables/cost endpoints) is the only external surface. A thin central Next.js app authenticates users, registers instances, proxies chat (Claude Agent SDK → instance MCP tools), reads dashboard data via HTTP, and writes prompt edits to the instance's `optimize/` directory.

**Tech Stack:** Existing Python 3.12 stack (unchanged), FastAPI + uvicorn (HTTP bridge, added), SQLite + aiosqlite (instance persistence, added), Next.js 15 + NextAuth v5 + Drizzle + small Postgres (central app, new), Fly Machines API (provisioning), Claude Agent SDK (chat), Tailwind + shadcn/ui (dashboard), Docker Compose for local dev.

**Scope gate (what v0 exits on):**
- One full weekly cycle runs to completion via the central app, hitting the OpenClaw instance, without manual intervention
- Dashboard shows the run + cost tracked to ±5% of Anthropic console
- Chat UI can trigger `run_weekly_cycle`, `edit_prompt`, and `harvest_kb` via MCP tools
- Prompt editor round-trips a Kai system prompt edit and shows the effect in a subsequent run

**Out of scope for v0:** Stripe billing, multi-customer (OpenClaw is the only instance), Seed/Mimic/Publisher/Meter agents (they're v1), OAuth connectors for Substack/Dev.to/LinkedIn/X, automated rolling updates across instances. All of these land in v1 or later.

---

## File structure (what gets created/modified)

### In `devrel-swarm/` repo (instance template)

**New:**
- `tools/storage.py` — SQLite persistence (deliverables, signals, cost_events, checkpoints)
- `tools/http_bridge.py` — FastAPI exposing MCP tools + run/deliverables/cost/prompt endpoints
- `workers/budget.py` — `BudgetGate` (tracking-only in v0)
- `workers/__init__.py`, `workers/pyproject.toml` — worker-side deps (fastapi, uvicorn, aiosqlite)
- `scripts/entrypoint.sh` — container start: run migrations, start bridge + scheduler
- `Dockerfile` — rebuilt for instance deployment with persistent `/data` volume

**Modified:**
- `agents/llm.py` — add `set_cost_sink()` + `_emit_cost()` for BudgetGate wiring
- `agents/atlas.py` — inject a `deliverable_sink` and `cost_sink` (small change, no file split)
- `tools/scheduler.py` — make cron entry configurable to call the HTTP bridge endpoint
- `pyproject.toml` — add `fastapi`, `uvicorn`, `aiosqlite` to `[project.optional-dependencies].bridge`
- `.dockerignore` — exclude `central-app/`

### In new `central-app/` (sibling directory inside the repo)

**New:**
- `central-app/package.json`, `tsconfig.json`, `next.config.ts`, `drizzle.config.ts`
- `central-app/src/auth.ts`, `middleware.ts`, `app/api/auth/[...nextauth]/route.ts`
- `central-app/src/db/schema.ts` (7 tables total: users, instances, chat_threads, chat_messages, sessions, accounts, verification_tokens)
- `central-app/src/db/client.ts`
- `central-app/src/lib/instance-client.ts` — typed HTTP client to talk to an instance's bridge
- `central-app/src/lib/fly-api.ts` — Fly Machines API client
- `central-app/src/lib/crypto.ts` — AES-256-GCM for encrypting instance API tokens
- `central-app/src/lib/chat.ts` — Claude Agent SDK chat loop wiring instance MCP tools
- `central-app/src/app/page.tsx` — instance list
- `central-app/src/app/instances/new/page.tsx` — manual-add + provisioning form
- `central-app/src/app/instances/[id]/page.tsx` — dashboard home
- `central-app/src/app/instances/[id]/deliverables/page.tsx`, `[dId]/page.tsx` — list + detail
- `central-app/src/app/instances/[id]/chat/page.tsx` — chat UI
- `central-app/src/app/instances/[id]/prompts/page.tsx` — prompt editor
- `central-app/src/app/api/instances/[id]/proxy/[...path]/route.ts` — auth-adding proxy to instance bridge
- `central-app/src/app/api/chat/[threadId]/route.ts` — streaming chat endpoint

---

## Phase A — Instance: SQLite storage + HTTP bridge (repo prep)

### Task A.1: SQLite storage layer

**Files:**
- Create: `/Users/macmini/devrel-swarm/tools/storage.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_storage.py`

- [ ] **Step 1: Write failing test**

Create `/Users/macmini/devrel-swarm/tests/test_storage.py`:

```python
"""SQLite storage layer for deliverables, signals, cost events, checkpoints."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

import pytest

from tools.storage import InstanceStorage


@pytest.fixture
async def storage():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        s = InstanceStorage(db_path=str(db_path))
        await s.init()
        yield s
        await s.close()


@pytest.mark.asyncio
async def test_create_job_and_record_cost(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.record_cost(
        job_id=job_id, agent="kai", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        cost_cents=1.05,
    )
    total = await storage.monthly_spend_cents()
    assert abs(total - 1.05) < 0.01


@pytest.mark.asyncio
async def test_insert_deliverable(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    deliverable_id = await storage.insert_deliverable(
        job_id=job_id, kind="tutorial", title="Hello", body_md="# hi",
        quality_score=8.2,
    )
    rows = await storage.list_deliverables(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == deliverable_id
    assert rows[0]["title"] == "Hello"


@pytest.mark.asyncio
async def test_update_job_status(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.update_job(job_id, status="completed")
    rows = await storage.list_jobs(limit=5)
    assert rows[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_save_and_latest_checkpoint(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.save_checkpoint(job_id, stage="s1", payload={"foo": "bar"})
    await storage.save_checkpoint(job_id, stage="s2", payload={"foo": "baz"})
    latest = await storage.latest_checkpoint(job_id)
    assert latest["stage"] == "s2"
    assert latest["payload"]["foo"] == "baz"
```

- [ ] **Step 2: Run, confirm ImportError**

```bash
cd /Users/macmini/devrel-swarm
pip install aiosqlite
pytest tests/test_storage.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'tools.storage'`

- [ ] **Step 3: Write `tools/storage.py`**

Create `/Users/macmini/devrel-swarm/tools/storage.py`:

```python
"""SQLite persistence for a single devrel-swarm instance.

Schema is flat + denormalized. Each instance has its own DB file under /data.
v0: no migration tooling — `init()` is idempotent CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    completed_at TEXT,
    cost_cents REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_checkpoints (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cost_events (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS cost_events_month_idx
    ON cost_events (substr(created_at, 1, 7));

CREATE TABLE IF NOT EXISTS deliverables (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    quality_score REAL,
    voice_score REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    raw_payload_json TEXT NOT NULL,
    sentiment TEXT,
    priority TEXT,
    theme_tags_json TEXT NOT NULL DEFAULT '[]',
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quality_events (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class InstanceStorage:
    """Async SQLite wrapper for a single instance's persistence."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(_DDL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("InstanceStorage not initialized; call init() first")
        return self._conn

    # -- Jobs ---------------------------------------------------------------
    async def create_job(self, kind: str) -> str:
        conn = self._require()
        job_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO jobs (id, kind, status, started_at) VALUES (?, ?, 'running', datetime('now'))",
            (job_id, kind),
        )
        await conn.commit()
        return job_id

    async def update_job(
        self,
        job_id: str,
        status: str | None = None,
        error: str | None = None,
        cost_cents: float | None = None,
    ) -> None:
        conn = self._require()
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
            if status in ("completed", "failed"):
                fields.append("completed_at = datetime('now')")
        if error is not None:
            fields.append("error_message = ?")
            params.append(error)
        if cost_cents is not None:
            fields.append("cost_cents = ?")
            params.append(cost_cents)
        if not fields:
            return
        params.append(job_id)
        await conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", tuple(params)
        )
        await conn.commit()

    async def list_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- Checkpoints --------------------------------------------------------
    async def save_checkpoint(self, job_id: str, stage: str, payload: dict) -> None:
        conn = self._require()
        await conn.execute(
            "INSERT INTO job_checkpoints (id, job_id, stage, payload_json) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), job_id, stage, json.dumps(payload)),
        )
        await conn.commit()

    async def latest_checkpoint(self, job_id: str) -> dict[str, Any] | None:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT stage, payload_json, created_at FROM job_checkpoints "
            "WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "stage": row["stage"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    # -- Cost events --------------------------------------------------------
    async def record_cost(
        self,
        job_id: str | None,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        cost_cents: float,
    ) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT INTO cost_events (
                id, job_id, agent, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                cost_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), job_id, agent, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                cost_cents,
            ),
        )
        await conn.commit()

    async def monthly_spend_cents(self) -> float:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_cents), 0) AS total FROM cost_events "
            "WHERE substr(created_at, 1, 7) = strftime('%Y-%m', 'now')"
        )
        row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    # -- Deliverables -------------------------------------------------------
    async def insert_deliverable(
        self,
        job_id: str | None,
        kind: str,
        title: str,
        body_md: str | None = None,
        quality_score: float | None = None,
        voice_score: float | None = None,
    ) -> str:
        conn = self._require()
        d_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO deliverables (id, job_id, kind, title, body_md, quality_score, voice_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (d_id, job_id, kind, title, body_md, quality_score, voice_score),
        )
        await conn.commit()
        return d_id

    async def list_deliverables(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM deliverables ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_deliverable(self, d_id: str) -> dict[str, Any] | None:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM deliverables WHERE id = ?", (d_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_storage.py -v
```

Expected: all 4 pass.

- [ ] **Step 5: Commit**

```bash
git add tools/storage.py tests/test_storage.py
git commit -m "feat: SQLite storage layer for instance persistence"
```

---

### Task A.2: BudgetGate (tracking-only in v0)

**Files:**
- Create: `/Users/macmini/devrel-swarm/workers/__init__.py`
- Create: `/Users/macmini/devrel-swarm/workers/budget.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_budget.py`

- [ ] **Step 1: Write failing test**

Create `/Users/macmini/devrel-swarm/tests/test_budget.py`:

```python
from unittest.mock import AsyncMock

import pytest

from workers.budget import BudgetGate, CostRecord


def test_cost_record_sonnet_pricing():
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    # $3 * 0.001 + $15 * 0.0005 = $0.0105 = 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


def test_cost_record_cache_pricing():
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=0, output_tokens=100,
        cache_creation_input_tokens=2000,
        cache_read_input_tokens=5000,
    )
    # 2000*$3.75/1M + 5000*$0.30/1M + 100*$15/1M = 0.0075+0.0015+0.0015 = 0.0105 = 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


@pytest.mark.asyncio
async def test_gate_tracks_without_blocking():
    storage = AsyncMock()
    storage.monthly_spend_cents = AsyncMock(return_value=0.0)
    storage.record_cost = AsyncMock()
    gate = BudgetGate(storage=storage, job_id="j1", block_on_exceed=False)
    allowed = await gate.check_and_record(
        CostRecord(model="claude-sonnet-4-6",
                   input_tokens=1000, output_tokens=500,
                   cache_creation_input_tokens=0, cache_read_input_tokens=0),
        agent="kai",
    )
    assert allowed is True
    storage.record_cost.assert_called_once()


@pytest.mark.asyncio
async def test_gate_blocks_when_over_cap():
    storage = AsyncMock()
    storage.monthly_spend_cents = AsyncMock(return_value=9900.0)  # $99 spent
    storage.record_cost = AsyncMock()
    gate = BudgetGate(
        storage=storage, job_id="j1",
        block_on_exceed=True, monthly_cap_cents=10000,
    )
    allowed = await gate.check_and_record(
        CostRecord(
            model="claude-sonnet-4-6",
            input_tokens=200_000, output_tokens=50_000,  # ~135 cents
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
        agent="kai",
    )
    assert allowed is False
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_budget.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Write modules**

Create `/Users/macmini/devrel-swarm/workers/__init__.py` (empty file).

Create `/Users/macmini/devrel-swarm/workers/budget.py`:

```python
"""Cost tracking + budget enforcement gate.

v0: tracking-only (block_on_exceed=False) — runs inside each instance.
v1: block_on_exceed=True enforces monthly_cap_cents set at provision time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Anthropic pricing, $ per 1M tokens. Update when pricing changes.
# Source: https://www.anthropic.com/pricing (verified 2026-04-18)
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0, "output": 75.0,
        "cache_write": 18.75, "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0, "output": 5.0,
        "cache_write": 1.25, "cache_read": 0.10,
    },
}


@dataclass
class CostRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int

    @property
    def cost_cents(self) -> float:
        prices = _PRICING.get(self.model)
        if prices is None:
            logger.warning("unknown model for pricing: %s — treating as sonnet", self.model)
            prices = _PRICING["claude-sonnet-4-6"]
        dollars = (
            self.input_tokens * prices["input"] / 1_000_000
            + self.output_tokens * prices["output"] / 1_000_000
            + self.cache_creation_input_tokens * prices["cache_write"] / 1_000_000
            + self.cache_read_input_tokens * prices["cache_read"] / 1_000_000
        )
        return dollars * 100


class BudgetExceeded(RuntimeError):
    pass


class BudgetGate:
    def __init__(
        self,
        storage: Any,
        job_id: str | None,
        block_on_exceed: bool = False,
        monthly_cap_cents: int = 0,
    ) -> None:
        self._storage = storage
        self._job_id = job_id
        self._block = block_on_exceed
        self._cap = monthly_cap_cents

    async def check_and_record(self, rec: CostRecord, agent: str) -> bool:
        if self._block and self._cap > 0:
            current = await self._storage.monthly_spend_cents()
            if current + rec.cost_cents > self._cap:
                logger.warning(
                    "BudgetGate blocked agent=%s projected=%.2f cap=%d",
                    agent, current + rec.cost_cents, self._cap,
                )
                return False

        await self._storage.record_cost(
            job_id=self._job_id, agent=agent, model=rec.model,
            input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
            cache_creation_input_tokens=rec.cache_creation_input_tokens,
            cache_read_input_tokens=rec.cache_read_input_tokens,
            cost_cents=rec.cost_cents,
        )
        return True
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_budget.py -v
```

Expected: all 4 pass.

- [ ] **Step 5: Commit**

```bash
git add workers/__init__.py workers/budget.py tests/test_budget.py
git commit -m "feat: BudgetGate with Anthropic pricing table (tracking-only v0)"
```

---

### Task A.3: Wire LLMClient → BudgetGate via cost_sink

**Files:**
- Modify: `/Users/macmini/devrel-swarm/agents/llm.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_llm_cost_sink.py`

- [ ] **Step 1: Inspect existing LLMClient**

```bash
cd /Users/macmini/devrel-swarm
grep -n "class LLMClient\|def set_agent\|usage\|TokenUsage\|messages.create" agents/llm.py | head -40
```

Note which method receives the Anthropic response with `.usage` on it — that's where `_emit_cost` gets called.

- [ ] **Step 2: Write the failing test**

Create `/Users/macmini/devrel-swarm/tests/test_llm_cost_sink.py`:

```python
import pytest

from agents.llm import LLMClient


@pytest.mark.asyncio
async def test_emit_cost_with_sink():
    captured: list[dict] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        captured.append({"agent": agent, "model": model, "usage": usage})

    client = LLMClient()
    client.set_agent("kai")
    client.set_cost_sink(sink)
    await client._emit_cost(
        model="claude-sonnet-4-6",
        input_tokens=100, output_tokens=50,
        cache_creation_input_tokens=10, cache_read_input_tokens=20,
    )
    assert len(captured) == 1
    assert captured[0]["agent"] == "kai"
    assert captured[0]["model"] == "claude-sonnet-4-6"
    assert captured[0]["usage"]["input_tokens"] == 100
    assert captured[0]["usage"]["cache_creation_input_tokens"] == 10


@pytest.mark.asyncio
async def test_emit_cost_noop_without_sink():
    client = LLMClient()
    # should not raise
    await client._emit_cost(
        model="claude-sonnet-4-6",
        input_tokens=1, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
```

- [ ] **Step 3: Add `set_cost_sink` + `_emit_cost` to LLMClient**

Edit `/Users/macmini/devrel-swarm/agents/llm.py`. In the `LLMClient.__init__` method, after the existing attribute initializations, add:

```python
self._cost_sink = None  # Optional[Callable[[str, str, dict], Awaitable[None]]]
```

Add these two methods to the class (location: alongside `set_agent`):

```python
def set_cost_sink(self, sink) -> None:
    """Register async callback (agent, model, usage_dict) -> None.
    
    Called after each Anthropic API response so BudgetGate can record cost.
    """
    self._cost_sink = sink

async def _emit_cost(
    self,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> None:
    if self._cost_sink is None:
        return
    await self._cost_sink(
        self._current_agent or "unknown",
        model,
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
        },
    )
```

In the Anthropic-calling method (grep for `messages.create` or `await self._client.messages` — the one that gets a response with `.usage`), immediately after the existing `TokenUsage` accounting call, add:

```python
await self._emit_cost(
    model=<model_variable_name_in_that_scope>,
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
    cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
)
```

If there's a `query()`-based Agent SDK path alongside a `messages.create()` path, wire `_emit_cost` in both.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_llm_cost_sink.py tests/ -v -x
```

Expected: new tests pass; existing LLM tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add agents/llm.py tests/test_llm_cost_sink.py
git commit -m "feat: LLMClient cost_sink hook for BudgetGate wiring"
```

---

### Task A.4: HTTP bridge — FastAPI wrapping MCP + adding instance endpoints

**Files:**
- Create: `/Users/macmini/devrel-swarm/workers/pyproject.toml`
- Create: `/Users/macmini/devrel-swarm/tools/http_bridge.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_http_bridge.py`

- [ ] **Step 1: Worker deps pyproject**

Create `/Users/macmini/devrel-swarm/workers/pyproject.toml`:

```toml
[project]
name = "devrel-swarm-bridge"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = [
    "fastapi==0.115.0",
    "uvicorn[standard]==0.32.0",
    "aiosqlite==0.20.0",
    "python-multipart==0.0.12",
]
```

- [ ] **Step 2: Install deps**

```bash
cd /Users/macmini/devrel-swarm
pip install fastapi uvicorn aiosqlite python-multipart httpx
```

- [ ] **Step 3: Write failing test**

Create `/Users/macmini/devrel-swarm/tests/test_http_bridge.py`:

```python
"""Tests for the HTTP bridge FastAPI app."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "instance.db")
        monkeypatch.setenv("INSTANCE_DB_PATH", db_path)
        monkeypatch.setenv("INSTANCE_API_TOKEN", "test-token-123")
        monkeypatch.setenv("OPTIMIZE_DIR", str(Path(d) / "optimize"))
        (Path(d) / "optimize" / "kai").mkdir(parents=True)
        (Path(d) / "optimize" / "kai" / "system_prompt.txt").write_text("seed prompt")

        from tools.http_bridge import create_app
        app = create_app()
        with TestClient(app) as c:
            yield c


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_protected_endpoints_require_token(client):
    res = client.get("/api/deliverables")
    assert res.status_code == 401


def test_list_jobs_empty(client):
    res = client.get("/api/jobs", headers={"authorization": "Bearer test-token-123"})
    assert res.status_code == 200
    assert res.json() == {"jobs": []}


def test_list_deliverables_empty(client):
    res = client.get(
        "/api/deliverables", headers={"authorization": "Bearer test-token-123"}
    )
    assert res.status_code == 200
    assert res.json() == {"deliverables": []}


def test_read_prompt(client):
    res = client.get(
        "/api/prompts/kai",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"agent": "kai", "prompt": "seed prompt"}


def test_write_prompt(client):
    res = client.put(
        "/api/prompts/kai",
        json={"prompt": "new prompt"},
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200

    # read back
    res2 = client.get(
        "/api/prompts/kai",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res2.json()["prompt"] == "new prompt"


def test_month_cost_empty(client):
    res = client.get(
        "/api/cost/month",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"cents": 0.0}
```

- [ ] **Step 4: Run, confirm failure**

```bash
pytest tests/test_http_bridge.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'tools.http_bridge'`

- [ ] **Step 5: Write `tools/http_bridge.py`**

Create `/Users/macmini/devrel-swarm/tools/http_bridge.py`:

```python
"""HTTP bridge around MCP server + instance state for the central app."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tools.storage import InstanceStorage

logger = logging.getLogger(__name__)

_storage: InstanceStorage | None = None


def _db_path() -> str:
    return os.environ.get("INSTANCE_DB_PATH", "/data/instance.db")


def _optimize_dir() -> Path:
    return Path(os.environ.get("OPTIMIZE_DIR", "optimize"))


def _expected_token() -> str:
    tok = os.environ.get("INSTANCE_API_TOKEN", "")
    if not tok:
        logger.warning("INSTANCE_API_TOKEN not set — bridge is unauthenticated")
    return tok


async def _get_storage() -> InstanceStorage:
    global _storage
    if _storage is None:
        _storage = InstanceStorage(db_path=_db_path())
        await _storage.init()
    return _storage


async def require_bearer(request: Request) -> None:
    expected = _expected_token()
    if not expected:
        return  # unauthenticated mode for tests; production must set the env
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    if header[len("Bearer "):].strip() != expected:
        raise HTTPException(status_code=401, detail="invalid token")


class PromptBody(BaseModel):
    prompt: str


class RunTriggerBody(BaseModel):
    kind: str = "weekly_cycle"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _get_storage()
        yield
        global _storage
        if _storage is not None:
            await _storage.close()
            _storage = None

    app = FastAPI(title="devrel-swarm instance bridge", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/jobs", dependencies=[Depends(require_bearer)])
    async def list_jobs(limit: int = 20) -> dict[str, Any]:
        storage = await _get_storage()
        return {"jobs": await storage.list_jobs(limit=limit)}

    @app.get("/api/deliverables", dependencies=[Depends(require_bearer)])
    async def list_deliverables(limit: int = 50) -> dict[str, Any]:
        storage = await _get_storage()
        return {"deliverables": await storage.list_deliverables(limit=limit)}

    @app.get("/api/deliverables/{d_id}", dependencies=[Depends(require_bearer)])
    async def get_deliverable(d_id: str) -> dict[str, Any]:
        storage = await _get_storage()
        row = await storage.get_deliverable(d_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return row

    @app.get("/api/cost/month", dependencies=[Depends(require_bearer)])
    async def month_cost() -> dict[str, float]:
        storage = await _get_storage()
        return {"cents": await storage.monthly_spend_cents()}

    @app.get("/api/prompts/{agent}", dependencies=[Depends(require_bearer)])
    async def read_prompt(agent: str) -> dict[str, str]:
        fp = _optimize_dir() / agent / "system_prompt.txt"
        if not fp.exists():
            raise HTTPException(status_code=404, detail="prompt not found")
        return {"agent": agent, "prompt": fp.read_text()}

    @app.put("/api/prompts/{agent}", dependencies=[Depends(require_bearer)])
    async def write_prompt(agent: str, body: PromptBody) -> dict[str, str]:
        fp = _optimize_dir() / agent / "system_prompt.txt"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body.prompt)
        return {"agent": agent, "status": "written"}

    @app.post("/api/run", dependencies=[Depends(require_bearer)])
    async def trigger_run(body: RunTriggerBody) -> dict[str, str]:
        """Trigger a weekly cycle in the background (Task A.5 wires this)."""
        storage = await _get_storage()
        job_id = await storage.create_job(kind=body.kind)
        # Dispatch is wired in Task A.5; for now just return job_id.
        return {"job_id": job_id, "status": "queued"}

    return app


# For `uvicorn tools.http_bridge:app`
app = create_app()
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_http_bridge.py -v
```

Expected: all 6 pass.

- [ ] **Step 7: Commit**

```bash
git add workers/pyproject.toml tools/http_bridge.py tests/test_http_bridge.py
git commit -m "feat: HTTP bridge exposing instance state + prompt editor endpoints"
```

---

### Task A.5: Wire `/api/run` to invoke Atlas with storage + BudgetGate

**Files:**
- Modify: `/Users/macmini/devrel-swarm/tools/http_bridge.py`
- Modify: `/Users/macmini/devrel-swarm/agents/atlas.py`
- Create: `/Users/macmini/devrel-swarm/tools/run_dispatch.py`

- [ ] **Step 1: Write the dispatch helper**

Create `/Users/macmini/devrel-swarm/tools/run_dispatch.py`:

```python
"""Bridges HTTP /api/run to Atlas.run_weekly_cycle with instance wiring."""

from __future__ import annotations

import logging
import os

from agents.atlas import Atlas
from tools.storage import InstanceStorage
from workers.budget import BudgetGate, CostRecord

logger = logging.getLogger(__name__)


async def run_weekly_cycle_in_instance(
    storage: InstanceStorage, job_id: str
) -> dict[str, str]:
    """Run one weekly cycle with cost + deliverable sinks wired to storage."""
    monthly_cap = int(os.environ.get("INSTANCE_MONTHLY_CAP_CENTS", "0"))

    gate = BudgetGate(
        storage=storage, job_id=job_id,
        block_on_exceed=False,  # v0 tracking-only
        monthly_cap_cents=monthly_cap,
    )

    async def cost_sink(agent: str, model: str, usage: dict) -> None:
        rec = CostRecord(
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_input_tokens=usage["cache_creation_input_tokens"],
            cache_read_input_tokens=usage["cache_read_input_tokens"],
        )
        await gate.check_and_record(rec, agent=agent)

    async def deliverable_sink(record: dict) -> None:
        await storage.insert_deliverable(
            job_id=job_id,
            kind=record.get("kind", "tutorial"),
            title=record.get("title", "Untitled"),
            body_md=record.get("body_md"),
            quality_score=record.get("quality_score"),
            voice_score=record.get("voice_score"),
        )

    async def checkpoint_sink(stage: str, payload: dict) -> None:
        await storage.save_checkpoint(job_id=job_id, stage=stage, payload=payload)

    atlas = Atlas()
    atlas.llm_client.set_cost_sink(cost_sink)
    atlas.set_deliverable_sink(deliverable_sink)
    atlas.set_checkpoint_sink(checkpoint_sink)

    try:
        await atlas.run_weekly_cycle()
        await storage.update_job(job_id, status="completed")
        return {"status": "completed"}
    except Exception as e:  # noqa: BLE001
        logger.exception("weekly cycle failed")
        await storage.update_job(job_id, status="failed", error=str(e))
        return {"status": "failed", "error": str(e)}
```

- [ ] **Step 2: Add sink hooks to Atlas (minimal change)**

In `/Users/macmini/devrel-swarm/agents/atlas.py`, inside `Atlas.__init__`, after existing init lines, add:

```python
self._deliverable_sink = None  # Optional[Callable[[dict], Awaitable[None]]]
self._checkpoint_sink = None   # Optional[Callable[[str, dict], Awaitable[None]]]
```

Add two methods to the class:

```python
def set_deliverable_sink(self, sink) -> None:
    self._deliverable_sink = sink

def set_checkpoint_sink(self, sink) -> None:
    self._checkpoint_sink = sink

async def _persist_deliverable(self, record: dict) -> None:
    if self._deliverable_sink is not None:
        await self._deliverable_sink(record)

async def _external_checkpoint(self, stage: str, payload: dict) -> None:
    if self._checkpoint_sink is not None:
        await self._checkpoint_sink(stage, payload)
```

Find each existing `self._checkpoint(stage=n)` call in `run_weekly_cycle` and add right after:

```python
await self._external_checkpoint(f"stage_{n}", ctx.to_dict())
```

After the Kai stage completes (look for where `ctx.kai_content` gets populated), add:

```python
for item in (ctx.kai_content.get("content") or []):
    if isinstance(item, dict):
        await self._persist_deliverable({
            "kind": item.get("kind", "tutorial"),
            "title": item.get("title", "Untitled"),
            "body_md": item.get("body", ""),
            "quality_score": item.get("score") or item.get("quality_score"),
        })
```

After Sentinel/brand audit stage, add:

```python
brand = ctx.okr_progress.get("brand_audit") if ctx.okr_progress else None
if brand:
    import json as _json
    await self._persist_deliverable({
        "kind": "brand_audit",
        "title": f"Brand audit · {ctx.week_of}",
        "body_md": _json.dumps(brand, indent=2, default=str),
        "quality_score": brand.get("overall_score"),
    })
```

- [ ] **Step 3: Wire `/api/run` to spawn the cycle in background**

Edit `/Users/macmini/devrel-swarm/tools/http_bridge.py`. Replace the stub `trigger_run` body with:

```python
import asyncio
from tools.run_dispatch import run_weekly_cycle_in_instance

@app.post("/api/run", dependencies=[Depends(require_bearer)])
async def trigger_run(body: RunTriggerBody) -> dict[str, str]:
    storage = await _get_storage()
    job_id = await storage.create_job(kind=body.kind)
    # Fire-and-forget; the job status updates via run_dispatch.
    asyncio.create_task(run_weekly_cycle_in_instance(storage, job_id))
    return {"job_id": job_id, "status": "queued"}
```

- [ ] **Step 4: Full test suite + local smoke**

```bash
pytest tests/ -v -x
```

Expected: all pass.

Manual smoke (from repo root, with `.env` populated with real ANTHROPIC_API_KEY etc.):

```bash
cd /Users/macmini/devrel-swarm
export INSTANCE_DB_PATH=/tmp/devrel-instance.db
export INSTANCE_API_TOKEN=smoke-token
export OPTIMIZE_DIR=$PWD/optimize
set -a; source .env; set +a
uvicorn tools.http_bridge:app --host 0.0.0.0 --port 8787 &
sleep 2

curl -fsS http://localhost:8787/health
# → {"status":"ok"}

curl -fsS -X POST -H "authorization: Bearer smoke-token" \
  -H "content-type: application/json" \
  -d '{"kind":"weekly_cycle"}' \
  http://localhost:8787/api/run
# → {"job_id":"...","status":"queued"}

# Wait a few minutes, then:
curl -fsS -H "authorization: Bearer smoke-token" \
  http://localhost:8787/api/jobs | jq .
# → {"jobs":[{"id":"...","status":"completed",...}]}

curl -fsS -H "authorization: Bearer smoke-token" \
  http://localhost:8787/api/deliverables | jq .
# → non-empty deliverables list
```

- [ ] **Step 5: Commit**

```bash
kill %1  # stop local uvicorn
git add tools/run_dispatch.py tools/http_bridge.py agents/atlas.py
git commit -m "feat: /api/run triggers weekly cycle with sinks → storage

Wires BudgetGate + deliverable + checkpoint sinks into Atlas so every
Anthropic call is tracked and every Kai/Sentinel output lands in SQLite."
```

---

### Task A.6: Dockerfile for instance deployment

**Files:**
- Modify: `/Users/macmini/devrel-swarm/Dockerfile`
- Create: `/Users/macmini/devrel-swarm/scripts/entrypoint.sh`
- Create: `/Users/macmini/devrel-swarm/.dockerignore`

- [ ] **Step 1: Read existing Dockerfile**

```bash
cd /Users/macmini/devrel-swarm && cat Dockerfile
```

Compare against the new needs: install bridge deps, expose 8787, run entrypoint that starts uvicorn + cron.

- [ ] **Step 2: Rewrite Dockerfile**

Replace `/Users/macmini/devrel-swarm/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron git ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml requirements.txt* ./
COPY workers/pyproject.toml workers/
RUN pip install --no-cache-dir -e . \
 && pip install --no-cache-dir fastapi uvicorn aiosqlite python-multipart

# Copy source
COPY agents/ agents/
COPY tools/ tools/
COPY workers/ workers/
COPY knowledge_base/ knowledge_base/
COPY optimize/ optimize/
COPY config/ config/
COPY scripts/ scripts/
RUN chmod +x scripts/entrypoint.sh

# Persistent volume for SQLite + mutable state
VOLUME ["/data"]
ENV INSTANCE_DB_PATH=/data/instance.db
ENV OPTIMIZE_DIR=/app/optimize
ENV PYTHONPATH=/app

EXPOSE 8787
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
```

- [ ] **Step 3: Write entrypoint**

Create `/Users/macmini/devrel-swarm/scripts/entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Ensure /data exists (Fly mounts the volume)
mkdir -p /data

# Initialize SQLite via a one-shot Python invocation (idempotent)
python - <<'PY'
import asyncio
import os
from tools.storage import InstanceStorage

async def _init():
    s = InstanceStorage(db_path=os.environ["INSTANCE_DB_PATH"])
    await s.init()
    await s.close()

asyncio.run(_init())
PY

# Install cron for weekly cycle if CRON_SCHEDULE is set (e.g. "0 9 * * 1")
if [[ -n "${CRON_SCHEDULE:-}" ]]; then
  TOKEN="${INSTANCE_API_TOKEN:-}"
  CRON_LINE="${CRON_SCHEDULE} root curl -fsS -X POST -H 'authorization: Bearer ${TOKEN}' -H 'content-type: application/json' -d '{\"kind\":\"weekly_cycle\"}' http://127.0.0.1:8787/api/run > /var/log/cron-last.log 2>&1"
  echo "${CRON_LINE}" > /etc/cron.d/devrel-cycle
  chmod 0644 /etc/cron.d/devrel-cycle
  cron
fi

# Start HTTP bridge (foreground)
exec uvicorn tools.http_bridge:app --host 0.0.0.0 --port 8787 --workers 1
```

- [ ] **Step 4: Write .dockerignore**

Create `/Users/macmini/devrel-swarm/.dockerignore`:

```
.git/
.venv/
workers/.venv/
__pycache__/
*.pyc
tests/
docs/
central-app/
docker-data/
*.md
!README.md
.env
.env.*
```

- [ ] **Step 5: Local Docker build + smoke**

```bash
cd /Users/macmini/devrel-swarm
docker build -t devrel-swarm-instance .
docker run --rm \
  -e INSTANCE_API_TOKEN=docker-smoke \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e GITHUB_TOKEN=$GITHUB_TOKEN \
  -v devrel_data:/data \
  -p 8787:8787 \
  devrel-swarm-instance &
sleep 5
curl -fsS http://localhost:8787/health
# → {"status":"ok"}
docker kill $(docker ps -q --filter ancestor=devrel-swarm-instance)
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile scripts/entrypoint.sh .dockerignore
git commit -m "feat: Dockerfile for instance deployment with persistent /data + cron"
```

---

## Phase B — Central app scaffold

### Task B.1: Next.js + Drizzle + NextAuth scaffold

**Files:**
- Create: `central-app/` (Next.js 15 app)

- [ ] **Step 1: Scaffold**

```bash
cd /Users/macmini/devrel-swarm
npx create-next-app@15 central-app --typescript --app --tailwind --eslint --src-dir --import-alias "@/*" --no-git --turbopack
```

- [ ] **Step 2: Install deps**

```bash
cd /Users/macmini/devrel-swarm/central-app
pnpm add drizzle-orm postgres next-auth@beta @anthropic-ai/sdk
pnpm add -D drizzle-kit @types/pg
```

- [ ] **Step 3: Write drizzle.config.ts**

Create `/Users/macmini/devrel-swarm/central-app/drizzle.config.ts`:

```typescript
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  dbCredentials: { url: process.env.DATABASE_URL! },
  strict: true,
});
```

- [ ] **Step 4: Env**

Create `/Users/macmini/devrel-swarm/central-app/.env.local`:

```
DATABASE_URL=postgresql://devrel:devrel@localhost:5433/devrel_central
AUTH_SECRET=<output of: openssl rand -base64 32>
AUTH_GITHUB_ID=<from GitHub OAuth app>
AUTH_GITHUB_SECRET=<from GitHub OAuth app>
ALLOWED_EMAILS=dovzhikova@gmail.com
ANTHROPIC_API_KEY=<your key>
# AES key for encrypting instance API tokens
INSTANCE_TOKEN_ENCRYPTION_KEY=<output of: openssl rand -hex 32>
```

Also spin a `devrel_central` database via the existing docker-compose Postgres (reuse container, different DB name):

```bash
cd /Users/macmini/devrel-swarm
docker compose up -d postgres  # existing docker-compose.yml doesn't exist yet from new plan; add it below
```

- [ ] **Step 5: Local Postgres for central app**

Create `/Users/macmini/devrel-swarm/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: devrel
      POSTGRES_PASSWORD: devrel
      POSTGRES_DB: devrel_central
    ports: ["5433:5432"]
    volumes:
      - ./docker-data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U devrel -d devrel_central"]
      interval: 5s
      retries: 5
```

```bash
docker compose up -d
```

- [ ] **Step 6: Commit**

```bash
git add central-app/ docker-compose.yml
git commit -m "feat: scaffold central-app with Next.js 15 + Drizzle + Postgres"
```

---

### Task B.2: Central Postgres schema (users + instances + chat)

**Files:**
- Create: `central-app/src/db/schema.ts`
- Create: `central-app/src/db/client.ts`

- [ ] **Step 1: Write schema**

Create `/Users/macmini/devrel-swarm/central-app/src/db/schema.ts`:

```typescript
import {
  pgTable, uuid, text, timestamp, jsonb, pgEnum, customType,
} from "drizzle-orm/pg-core";

const bytea = customType<{ data: Buffer }>({
  dataType() { return "bytea"; },
});

export const instanceStatusEnum = pgEnum("instance_status", [
  "provisioning", "active", "paused", "failed", "deleted",
]);

export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  email: text("email").notNull().unique(),
  name: text("name"),
  image: text("image"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const instances = pgTable("instances", {
  id: uuid("id").defaultRandom().primaryKey(),
  ownerUserId: uuid("owner_user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  slug: text("slug").notNull().unique(),
  flyAppName: text("fly_app_name"),
  flyAppUrl: text("fly_app_url"),
  apiTokenEncrypted: bytea("api_token_encrypted"),
  status: instanceStatusEnum("status").notNull().default("provisioning"),
  productName: text("product_name").notNull(),
  cronSchedule: text("cron_schedule").notNull().default("0 9 * * 1"),
  monthlyCapCents: jsonb("monthly_cap_cents").$type<number>().default(10000),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  provisionedAt: timestamp("provisioned_at", { withTimezone: true }),
});

export const chatThreads = pgTable("chat_threads", {
  id: uuid("id").defaultRandom().primaryKey(),
  instanceId: uuid("instance_id").notNull().references(() => instances.id, { onDelete: "cascade" }),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  title: text("title").notNull().default("New chat"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const chatMessages = pgTable("chat_messages", {
  id: uuid("id").defaultRandom().primaryKey(),
  threadId: uuid("thread_id").notNull().references(() => chatThreads.id, { onDelete: "cascade" }),
  role: text("role").notNull(), // 'user' | 'assistant' | 'tool'
  contentJson: jsonb("content_json").$type<Record<string, unknown>>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});
```

- [ ] **Step 2: Write db client**

Create `/Users/macmini/devrel-swarm/central-app/src/db/client.ts`:

```typescript
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const client = postgres(process.env.DATABASE_URL!, { max: 10 });
export const db = drizzle(client, { schema });
export * from "./schema";
```

- [ ] **Step 3: Generate + apply migration**

```bash
cd /Users/macmini/devrel-swarm/central-app
pnpm drizzle-kit generate
pnpm drizzle-kit migrate
```

Verify:

```bash
docker compose exec postgres psql -U devrel -d devrel_central -c "\dt"
```

Expected: 4 tables + `instance_status` enum.

- [ ] **Step 4: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/db/ central-app/drizzle/
git commit -m "feat: central schema — users, instances, chat_threads, chat_messages"
```

---

### Task B.3: NextAuth with GitHub + email allowlist

**Files:**
- Create: `central-app/src/auth.ts`
- Create: `central-app/src/middleware.ts`
- Create: `central-app/src/app/api/auth/[...nextauth]/route.ts`

- [ ] **Step 1: Register GitHub OAuth app**

Manual: `https://github.com/settings/developers` → New OAuth App, callback `http://localhost:3000/api/auth/callback/github`. Save client ID + secret into `.env.local`.

- [ ] **Step 2: Auth config**

Create `/Users/macmini/devrel-swarm/central-app/src/auth.ts`:

```typescript
import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";
import { db, users } from "@/db/client";
import { eq } from "drizzle-orm";

const ALLOWED = (process.env.ALLOWED_EMAILS ?? "")
  .split(",").map(s => s.trim().toLowerCase()).filter(Boolean);

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [GitHub],
  session: { strategy: "jwt" },
  callbacks: {
    async signIn({ user }) {
      const email = user.email?.toLowerCase();
      if (!email || !ALLOWED.includes(email)) return false;
      // upsert user row
      const [existing] = await db.select().from(users).where(eq(users.email, email)).limit(1);
      if (!existing) {
        await db.insert(users).values({
          email, name: user.name ?? null, image: user.image ?? null,
        });
      }
      return true;
    },
    async jwt({ token, user }) {
      if (user?.email) {
        const [row] = await db.select().from(users).where(eq(users.email, user.email.toLowerCase())).limit(1);
        if (row) token.userId = row.id;
      }
      return token;
    },
    async session({ session, token }) {
      if (token.userId) (session.user as any).id = token.userId;
      return session;
    },
  },
});
```

- [ ] **Step 3: Handler + middleware**

Create `/Users/macmini/devrel-swarm/central-app/src/app/api/auth/[...nextauth]/route.ts`:

```typescript
import { handlers } from "@/auth";
export const { GET, POST } = handlers;
```

Create `/Users/macmini/devrel-swarm/central-app/src/middleware.ts`:

```typescript
import { auth } from "@/auth";

export default auth((req) => {
  const path = req.nextUrl.pathname;
  if (path.startsWith("/api/auth") || path.startsWith("/api/inngest")) return;
  if (!req.auth) {
    return Response.redirect(new URL("/api/auth/signin", req.nextUrl.origin));
  }
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/auth).*)"],
};
```

- [ ] **Step 4: Smoke**

```bash
cd /Users/macmini/devrel-swarm/central-app
pnpm dev
```

Visit `http://localhost:3000` — redirects to GitHub sign-in; allowlisted email passes.

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/auth.ts central-app/src/middleware.ts central-app/src/app/api/auth/
git commit -m "feat: NextAuth GitHub provider with email allowlist + user upsert"
```

---

## Phase C — InstanceClient + dashboard surfaces

### Task C.1: Encryption helper + InstanceClient

**Files:**
- Create: `central-app/src/lib/crypto.ts`
- Create: `central-app/src/lib/instance-client.ts`

- [ ] **Step 1: Encryption helper**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/crypto.ts`:

```typescript
import crypto from "node:crypto";

const ALG = "aes-256-gcm";

function key(): Buffer {
  const hex = process.env.INSTANCE_TOKEN_ENCRYPTION_KEY;
  if (!hex) throw new Error("INSTANCE_TOKEN_ENCRYPTION_KEY not set");
  const b = Buffer.from(hex, "hex");
  if (b.length !== 32) throw new Error("encryption key must be 32 bytes hex");
  return b;
}

export function encryptToken(plain: string): Buffer {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALG, key(), iv);
  const ct = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  // format: iv(12) || tag(16) || ciphertext
  return Buffer.concat([iv, tag, ct]);
}

export function decryptToken(blob: Buffer): string {
  const iv = blob.subarray(0, 12);
  const tag = blob.subarray(12, 28);
  const ct = blob.subarray(28);
  const decipher = crypto.createDecipheriv(ALG, key(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
}
```

- [ ] **Step 2: InstanceClient**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/instance-client.ts`:

```typescript
export type Job = {
  id: string;
  kind: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  cost_cents: number;
  error_message: string | null;
  created_at: string;
};

export type Deliverable = {
  id: string;
  job_id: string | null;
  kind: string;
  title: string;
  body_md: string | null;
  status: string;
  quality_score: number | null;
  voice_score: number | null;
  created_at: string;
};

export class InstanceClient {
  constructor(private baseUrl: string, private token: string) {}

  private async json<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        authorization: `Bearer ${this.token}`,
        "content-type": "application/json",
      },
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`instance ${path} returned ${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as T;
  }

  health = () => this.json<{ status: string }>("/health");
  listJobs = (limit = 20) => this.json<{ jobs: Job[] }>(`/api/jobs?limit=${limit}`);
  listDeliverables = (limit = 50) =>
    this.json<{ deliverables: Deliverable[] }>(`/api/deliverables?limit=${limit}`);
  getDeliverable = (id: string) => this.json<Deliverable>(`/api/deliverables/${id}`);
  monthCostCents = () => this.json<{ cents: number }>("/api/cost/month");
  readPrompt = (agent: string) => this.json<{ agent: string; prompt: string }>(`/api/prompts/${agent}`);
  writePrompt = (agent: string, prompt: string) =>
    this.json<{ agent: string; status: string }>(`/api/prompts/${agent}`, {
      method: "PUT",
      body: JSON.stringify({ prompt }),
    });
  triggerRun = (kind = "weekly_cycle") =>
    this.json<{ job_id: string; status: string }>("/api/run", {
      method: "POST",
      body: JSON.stringify({ kind }),
    });
}
```

- [ ] **Step 3: Commit**

```bash
git add central-app/src/lib/
git commit -m "feat: encryption helper + typed InstanceClient for HTTP bridge"
```

---

### Task C.2: "Add instance" manual form (first OpenClaw flow)

**Files:**
- Create: `central-app/src/app/page.tsx`
- Create: `central-app/src/app/instances/new/page.tsx`
- Create: `central-app/src/app/instances/new/actions.ts`

- [ ] **Step 1: Server action to add instance**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/new/actions.ts`:

```typescript
"use server";

import { redirect } from "next/navigation";
import { db, instances, users } from "@/db/client";
import { auth } from "@/auth";
import { encryptToken } from "@/lib/crypto";
import { eq } from "drizzle-orm";

export async function addInstanceManually(formData: FormData) {
  const session = await auth();
  if (!session?.user?.email) throw new Error("unauthenticated");
  const [user] = await db.select().from(users)
    .where(eq(users.email, session.user.email.toLowerCase())).limit(1);
  if (!user) throw new Error("no user row");

  const slug = String(formData.get("slug") ?? "").trim();
  const url = String(formData.get("url") ?? "").trim();
  const token = String(formData.get("token") ?? "").trim();
  const productName = String(formData.get("productName") ?? "").trim();

  if (!slug || !url || !token || !productName) {
    throw new Error("missing field");
  }

  const [inserted] = await db.insert(instances).values({
    ownerUserId: user.id,
    slug,
    flyAppUrl: url,
    apiTokenEncrypted: encryptToken(token),
    status: "active",
    productName,
    provisionedAt: new Date(),
  }).returning({ id: instances.id });

  redirect(`/instances/${inserted.id}`);
}
```

- [ ] **Step 2: Form page**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/new/page.tsx`:

```tsx
import { addInstanceManually } from "./actions";

export default function NewInstancePage() {
  return (
    <main className="p-8 max-w-lg mx-auto">
      <h1 className="text-2xl font-semibold mb-6">Add an instance</h1>
      <p className="text-sm text-gray-500 mb-6">
        v0: paste the URL + API token of a running devrel-swarm instance. v0.5
        replaces this with automated Fly provisioning.
      </p>
      <form action={addInstanceManually} className="space-y-4">
        <label className="block">
          <span className="text-sm">Slug</span>
          <input name="slug" required
            className="mt-1 block w-full border rounded px-3 py-2"
            placeholder="openclaw" />
        </label>
        <label className="block">
          <span className="text-sm">Product name</span>
          <input name="productName" required
            className="mt-1 block w-full border rounded px-3 py-2"
            placeholder="OpenClaw" />
        </label>
        <label className="block">
          <span className="text-sm">Instance URL</span>
          <input name="url" required
            className="mt-1 block w-full border rounded px-3 py-2"
            placeholder="https://openclaw.fly.dev" />
        </label>
        <label className="block">
          <span className="text-sm">API token</span>
          <input name="token" required type="password"
            className="mt-1 block w-full border rounded px-3 py-2" />
        </label>
        <button className="px-4 py-2 rounded bg-black text-white text-sm">
          Add instance
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 3: Home page (instance list)**

Create `/Users/macmini/devrel-swarm/central-app/src/app/page.tsx`:

```tsx
import Link from "next/link";
import { auth } from "@/auth";
import { db, instances, users } from "@/db/client";
import { eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

export default async function Home() {
  const session = await auth();
  const email = session?.user?.email?.toLowerCase();
  if (!email) return null;

  const [user] = await db.select().from(users).where(eq(users.email, email)).limit(1);
  const rows = user
    ? await db.select().from(instances).where(eq(instances.ownerUserId, user.id))
    : [];

  return (
    <main className="p-8 max-w-4xl mx-auto">
      <header className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-semibold">Your instances</h1>
        <Link href="/instances/new" className="px-3 py-2 rounded bg-black text-white text-sm">
          + Add instance
        </Link>
      </header>
      <div className="border rounded divide-y">
        {rows.map(r => (
          <Link key={r.id} href={`/instances/${r.id}`}
            className="p-4 flex justify-between hover:bg-gray-50">
            <div>
              <div className="font-medium">{r.slug}</div>
              <div className="text-xs text-gray-500">{r.productName}</div>
            </div>
            <div className="text-sm">{r.status}</div>
          </Link>
        ))}
        {rows.length === 0 && (
          <div className="p-4 text-sm text-gray-500">No instances yet.</div>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add central-app/src/app/page.tsx central-app/src/app/instances/new/
git commit -m "feat: home (instance list) + manual add-instance form"
```

---

### Task C.3: Instance dashboard (home + deliverables)

**Files:**
- Create: `central-app/src/lib/get-instance.ts`
- Create: `central-app/src/app/instances/[id]/page.tsx`
- Create: `central-app/src/app/instances/[id]/deliverables/page.tsx`
- Create: `central-app/src/app/instances/[id]/deliverables/[dId]/page.tsx`

- [ ] **Step 1: Instance loader**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/get-instance.ts`:

```typescript
import { db, instances, users } from "@/db/client";
import { auth } from "@/auth";
import { decryptToken } from "@/lib/crypto";
import { InstanceClient } from "@/lib/instance-client";
import { and, eq } from "drizzle-orm";

export async function loadInstanceForUser(instanceId: string) {
  const session = await auth();
  const email = session?.user?.email?.toLowerCase();
  if (!email) throw new Error("unauth");
  const [user] = await db.select().from(users).where(eq(users.email, email)).limit(1);
  if (!user) throw new Error("no user");
  const [inst] = await db.select().from(instances)
    .where(and(eq(instances.id, instanceId), eq(instances.ownerUserId, user.id)))
    .limit(1);
  if (!inst) throw new Error("not found");
  const token = inst.apiTokenEncrypted
    ? decryptToken(Buffer.from(inst.apiTokenEncrypted as unknown as Uint8Array))
    : "";
  const client = new InstanceClient(inst.flyAppUrl ?? "", token);
  return { instance: inst, client };
}
```

- [ ] **Step 2: Dashboard home**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/page.tsx`:

```tsx
import Link from "next/link";
import { loadInstanceForUser } from "@/lib/get-instance";

export const dynamic = "force-dynamic";

async function triggerRun(id: string) {
  "use server";
  const { client } = await loadInstanceForUser(id);
  await client.triggerRun();
}

export default async function InstanceHome({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { instance, client } = await loadInstanceForUser(id);

  const [{ jobs }, cost] = await Promise.all([
    client.listJobs(5).catch(() => ({ jobs: [] })),
    client.monthCostCents().catch(() => ({ cents: 0 })),
  ]);

  const run = triggerRun.bind(null, id);

  return (
    <main className="p-8 max-w-5xl mx-auto space-y-8">
      <header className="flex justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{instance.slug}</h1>
          <p className="text-sm text-gray-500">
            {instance.productName} · month spend: ${(cost.cents / 100).toFixed(2)}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href={`/instances/${id}/deliverables`} className="px-3 py-2 rounded border text-sm">Deliverables</Link>
          <Link href={`/instances/${id}/chat`} className="px-3 py-2 rounded border text-sm">Chat</Link>
          <Link href={`/instances/${id}/prompts`} className="px-3 py-2 rounded border text-sm">Prompts</Link>
          <form action={run}>
            <button className="px-3 py-2 rounded bg-black text-white text-sm">
              Run weekly cycle
            </button>
          </form>
        </div>
      </header>

      <section>
        <h2 className="font-medium mb-3">Recent runs</h2>
        <div className="border rounded divide-y">
          {jobs.map(j => (
            <div key={j.id} className="p-3 flex justify-between text-sm">
              <div className="font-mono text-xs text-gray-500">{j.id.slice(0, 8)}</div>
              <div>{j.kind}</div>
              <div>{j.status}</div>
              <div>${(Number(j.cost_cents) / 100).toFixed(2)}</div>
            </div>
          ))}
          {jobs.length === 0 && (
            <div className="p-3 text-sm text-gray-500">No runs yet.</div>
          )}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Deliverables list**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/deliverables/page.tsx`:

```tsx
import Link from "next/link";
import { loadInstanceForUser } from "@/lib/get-instance";

export const dynamic = "force-dynamic";

export default async function Deliverables({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { client } = await loadInstanceForUser(id);
  const { deliverables } = await client.listDeliverables(50).catch(() => ({ deliverables: [] }));

  return (
    <main className="p-8 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-6">Deliverables</h1>
      <div className="border rounded divide-y">
        {deliverables.map(d => (
          <Link key={d.id}
            href={`/instances/${id}/deliverables/${d.id}`}
            className="p-4 flex justify-between hover:bg-gray-50">
            <div>
              <div className="font-medium">{d.title}</div>
              <div className="text-xs text-gray-500">
                {d.kind} · {new Date(d.created_at).toLocaleString()}
              </div>
            </div>
            <div className="text-sm">
              {d.status} · {d.quality_score ? d.quality_score.toFixed(1) : "—"}
            </div>
          </Link>
        ))}
        {deliverables.length === 0 && (
          <div className="p-4 text-sm text-gray-500">No deliverables yet.</div>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Deliverable detail**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/deliverables/[dId]/page.tsx`:

```tsx
import { loadInstanceForUser } from "@/lib/get-instance";

export const dynamic = "force-dynamic";

export default async function DeliverableDetail({
  params,
}: {
  params: Promise<{ id: string; dId: string }>;
}) {
  const { id, dId } = await params;
  const { client } = await loadInstanceForUser(id);
  const d = await client.getDeliverable(dId);

  return (
    <main className="p-8 max-w-3xl mx-auto space-y-4">
      <h1 className="text-2xl font-semibold">{d.title}</h1>
      <div className="text-xs text-gray-500">
        {d.kind} · {d.status} · quality{" "}
        {d.quality_score ? d.quality_score.toFixed(1) : "—"}/10
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm bg-gray-50 p-4 rounded">
        {d.body_md ?? "(no body)"}
      </pre>
    </main>
  );
}
```

- [ ] **Step 5: Smoke test end-to-end**

With the instance running locally (from Task A.5) and central app running:

```bash
# Terminal 1: instance
cd /Users/macmini/devrel-swarm
set -a; source .env; set +a
export INSTANCE_DB_PATH=/tmp/openclaw.db
export INSTANCE_API_TOKEN=local-openclaw-token
export OPTIMIZE_DIR=$PWD/optimize
uvicorn tools.http_bridge:app --host 0.0.0.0 --port 8787

# Terminal 2: central app
cd /Users/macmini/devrel-swarm/central-app
pnpm dev
```

Browser:
1. Visit `http://localhost:3000`, sign in
2. Click "+ Add instance", fill in `slug=openclaw, productName=OpenClaw, url=http://localhost:8787, token=local-openclaw-token`
3. Dashboard loads for the instance
4. Click "Run weekly cycle", wait, see jobs populate
5. Click "Deliverables" — list populates

- [ ] **Step 6: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/lib/get-instance.ts central-app/src/app/instances/
git commit -m "feat: instance dashboard with jobs + deliverables list + detail"
```

---

## Phase D — Chat interface (Claude Agent SDK → instance MCP tools)

### Task D.1: Chat server action + streaming route

**Files:**
- Create: `central-app/src/lib/chat.ts`
- Create: `central-app/src/app/api/chat/[threadId]/route.ts`
- Create: `central-app/src/app/instances/[id]/chat/page.tsx`

**Approach:** v0 uses a simple tool-calling loop with the Anthropic SDK. The "tools" it exposes are a curated subset of the HTTP bridge endpoints (list deliverables, trigger run, read/write prompt, get cost). Full MCP protocol passthrough lands in v1.

- [ ] **Step 1: Write the chat tool definitions + loop**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/chat.ts`:

```typescript
import Anthropic from "@anthropic-ai/sdk";
import { InstanceClient } from "./instance-client";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

type ChatTool = {
  name: string;
  description: string;
  input_schema: Anthropic.Messages.Tool.InputSchema;
};

const TOOLS: ChatTool[] = [
  {
    name: "list_deliverables",
    description: "List recent deliverables (tutorials, digests, brand audits)",
    input_schema: {
      type: "object",
      properties: { limit: { type: "number", default: 10 } },
    },
  },
  {
    name: "trigger_weekly_cycle",
    description: "Start a new weekly DevRel cycle run",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "read_agent_prompt",
    description: "Read the current system prompt for a named agent (kai, sage, iris, echo, sentinel, rex)",
    input_schema: {
      type: "object",
      properties: { agent: { type: "string" } },
      required: ["agent"],
    },
  },
  {
    name: "write_agent_prompt",
    description: "Overwrite an agent's system prompt. Use with care — this affects all future runs.",
    input_schema: {
      type: "object",
      properties: {
        agent: { type: "string" },
        prompt: { type: "string" },
      },
      required: ["agent", "prompt"],
    },
  },
  {
    name: "month_cost_cents",
    description: "Get this month's cumulative Anthropic spend in cents",
    input_schema: { type: "object", properties: {} },
  },
];

async function runTool(
  client: InstanceClient,
  name: string,
  input: Record<string, unknown>,
): Promise<string> {
  try {
    switch (name) {
      case "list_deliverables":
        return JSON.stringify(await client.listDeliverables(Number(input.limit ?? 10)));
      case "trigger_weekly_cycle":
        return JSON.stringify(await client.triggerRun());
      case "read_agent_prompt":
        return JSON.stringify(await client.readPrompt(String(input.agent)));
      case "write_agent_prompt":
        return JSON.stringify(
          await client.writePrompt(String(input.agent), String(input.prompt))
        );
      case "month_cost_cents":
        return JSON.stringify(await client.monthCostCents());
      default:
        return JSON.stringify({ error: `unknown tool: ${name}` });
    }
  } catch (e) {
    return JSON.stringify({ error: String(e) });
  }
}

const SYSTEM_PROMPT = `You are the control interface for a DevRel AI swarm.
The user owns an instance of devrel-swarm that runs agents (Sage, Echo, Iris, Kai, Sentinel, Rex, etc.).
You help them: inspect recent runs + deliverables, trigger new cycles, read/edit agent system prompts, track cost.

When the user asks to change a prompt, show them the current prompt first and confirm the new version before writing.
When the user asks to run a cycle, confirm cost implications if month spend is already high.
Be concise. Use tools liberally — don't ask for info you can fetch.`;

export type ClientMessage = {
  role: "user" | "assistant";
  content: string;
};

export async function* streamChat(
  client: InstanceClient,
  history: ClientMessage[],
  userText: string,
) {
  const messages: Anthropic.Messages.MessageParam[] = [
    ...history.map(m => ({ role: m.role, content: m.content })),
    { role: "user", content: userText },
  ];

  // Tool-calling loop (v0: at most 6 turns of tool use to prevent runaway)
  for (let turn = 0; turn < 6; turn++) {
    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 2048,
      system: SYSTEM_PROMPT,
      tools: TOOLS,
      messages,
    });

    // Yield text blocks as they arrive
    for (const block of response.content) {
      if (block.type === "text") {
        yield { type: "text", text: block.text };
      }
    }

    // If the model didn't call a tool, we're done
    const toolUse = response.content.find(b => b.type === "tool_use") as
      | Anthropic.Messages.ToolUseBlock
      | undefined;
    if (!toolUse || response.stop_reason === "end_turn") {
      return;
    }

    // Record assistant turn + execute tool
    messages.push({ role: "assistant", content: response.content });
    yield { type: "tool_use", name: toolUse.name, input: toolUse.input };

    const result = await runTool(client, toolUse.name, toolUse.input as Record<string, unknown>);
    yield { type: "tool_result", tool: toolUse.name, result };

    messages.push({
      role: "user",
      content: [{ type: "tool_result", tool_use_id: toolUse.id, content: result }],
    });
  }
}
```

- [ ] **Step 2: Streaming chat route**

Create `/Users/macmini/devrel-swarm/central-app/src/app/api/chat/[threadId]/route.ts`:

```typescript
import { NextRequest } from "next/server";
import { db, chatMessages, chatThreads } from "@/db/client";
import { loadInstanceForUser } from "@/lib/get-instance";
import { streamChat, ClientMessage } from "@/lib/chat";
import { asc, eq } from "drizzle-orm";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> }
) {
  const { threadId } = await params;
  const { text } = (await req.json()) as { text: string };

  const [thread] = await db.select().from(chatThreads).where(eq(chatThreads.id, threadId)).limit(1);
  if (!thread) return new Response("thread not found", { status: 404 });

  const { client } = await loadInstanceForUser(thread.instanceId);

  const prior = await db.select().from(chatMessages)
    .where(eq(chatMessages.threadId, threadId))
    .orderBy(asc(chatMessages.createdAt));
  const history: ClientMessage[] = prior
    .filter(m => m.role === "user" || m.role === "assistant")
    .map(m => ({
      role: m.role as "user" | "assistant",
      content: String((m.contentJson as { text?: string }).text ?? ""),
    }));

  await db.insert(chatMessages).values({
    threadId, role: "user", contentJson: { text },
  });

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      let finalText = "";
      try {
        for await (const chunk of streamChat(client, history, text)) {
          controller.enqueue(encoder.encode(JSON.stringify(chunk) + "\n"));
          if (chunk.type === "text") finalText += chunk.text;
        }
        await db.insert(chatMessages).values({
          threadId, role: "assistant", contentJson: { text: finalText },
        });
      } catch (e) {
        controller.enqueue(
          encoder.encode(JSON.stringify({ type: "error", error: String(e) }) + "\n")
        );
      } finally {
        controller.close();
      }
    },
  });
  return new Response(stream, {
    headers: { "content-type": "application/x-ndjson" },
  });
}
```

- [ ] **Step 3: Chat UI page**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/chat/page.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

type ChunkMsg =
  | { type: "text"; text: string }
  | { type: "tool_use"; name: string; input: unknown }
  | { type: "tool_result"; tool: string; result: string }
  | { type: "error"; error: string };

type ViewMsg =
  | { role: "user" | "assistant"; text: string }
  | { role: "tool"; summary: string };

export default function ChatPage() {
  const { id } = useParams() as { id: string };
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ViewMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // Create a thread on mount (v0: one thread per page load)
  useEffect(() => {
    (async () => {
      const res = await fetch(`/api/instances/${id}/threads`, { method: "POST" });
      const { threadId } = (await res.json()) as { threadId: string };
      setThreadId(threadId);
    })();
  }, [id]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!threadId || !input.trim() || busy) return;
    const userText = input;
    setInput("");
    setMessages(m => [...m, { role: "user", text: userText }]);
    setBusy(true);

    let assistantBuf = "";
    setMessages(m => [...m, { role: "assistant", text: "" }]);

    const res = await fetch(`/api/chat/${threadId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: userText }),
    });
    if (!res.body) { setBusy(false); return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += dec.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const chunk = JSON.parse(line) as ChunkMsg;
        if (chunk.type === "text") {
          assistantBuf += chunk.text;
          setMessages(m => {
            const copy = [...m];
            copy[copy.length - 1] = { role: "assistant", text: assistantBuf };
            return copy;
          });
        } else if (chunk.type === "tool_use") {
          setMessages(m => [...m, {
            role: "tool",
            summary: `→ calling ${chunk.name}(${JSON.stringify(chunk.input)})`,
          }]);
        } else if (chunk.type === "tool_result") {
          setMessages(m => [...m, {
            role: "tool",
            summary: `← ${chunk.tool} result`,
          }]);
        }
      }
    }
    setBusy(false);
  }

  return (
    <main className="p-8 max-w-3xl mx-auto h-screen flex flex-col">
      <h1 className="text-2xl font-semibold mb-4">Chat</h1>
      <div className="flex-1 overflow-y-auto space-y-3 border rounded p-4 bg-white">
        {messages.map((m, i) => (
          <div key={i} className={
            m.role === "user" ? "text-right" :
            m.role === "tool" ? "text-xs text-gray-500 font-mono" :
            ""
          }>
            <div className={
              m.role === "user"
                ? "inline-block bg-black text-white rounded px-3 py-2 text-sm max-w-[80%]"
                : m.role === "assistant"
                  ? "whitespace-pre-wrap text-sm"
                  : ""
            }>
              {m.role === "tool" ? m.summary : (m as { text: string }).text}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form
        className="mt-4 flex gap-2"
        onSubmit={e => { e.preventDefault(); send(); }}
      >
        <input
          className="flex-1 border rounded px-3 py-2 text-sm"
          placeholder="Ask your swarm…"
          value={input}
          onChange={e => setInput(e.target.value)}
          disabled={busy}
        />
        <button
          className="px-4 py-2 rounded bg-black text-white text-sm disabled:opacity-50"
          disabled={busy || !threadId}
        >Send</button>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: Thread-create endpoint**

Create `/Users/macmini/devrel-swarm/central-app/src/app/api/instances/[id]/threads/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { db, chatThreads, users } from "@/db/client";
import { eq } from "drizzle-orm";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const session = await auth();
  if (!session?.user?.email) return new NextResponse("unauth", { status: 401 });
  const [user] = await db.select().from(users)
    .where(eq(users.email, session.user.email.toLowerCase())).limit(1);
  if (!user) return new NextResponse("no user", { status: 401 });
  const [t] = await db.insert(chatThreads).values({
    instanceId: id, userId: user.id,
  }).returning({ id: chatThreads.id });
  return NextResponse.json({ threadId: t.id });
}
```

- [ ] **Step 5: Smoke test**

From the instance dashboard, click "Chat". Type "list my recent deliverables" — the model should call `list_deliverables`, show a tool-use line, and summarize.

Type "what's my spend this month?" — should call `month_cost_cents`.

- [ ] **Step 6: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/lib/chat.ts central-app/src/app/api/ central-app/src/app/instances/[id]/chat/
git commit -m "feat: chat interface with Claude tool-calling against instance bridge"
```

---

## Phase E — Prompt editor

### Task E.1: Prompt editor UI

**Files:**
- Create: `central-app/src/app/instances/[id]/prompts/page.tsx`
- Create: `central-app/src/app/instances/[id]/prompts/[agent]/page.tsx`
- Create: `central-app/src/app/instances/[id]/prompts/[agent]/actions.ts`

- [ ] **Step 1: Agent list**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/prompts/page.tsx`:

```tsx
import Link from "next/link";

const AGENTS = ["sage", "echo", "iris", "kai", "sentinel", "rex", "watchdog"];

export default async function Prompts({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="p-8 max-w-3xl mx-auto">
      <h1 className="text-2xl font-semibold mb-6">Prompts</h1>
      <ul className="border rounded divide-y">
        {AGENTS.map(a => (
          <li key={a}>
            <Link href={`/instances/${id}/prompts/${a}`}
              className="block p-4 hover:bg-gray-50 flex justify-between">
              <span className="font-medium">{a}</span>
              <span className="text-xs text-gray-500">edit →</span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

- [ ] **Step 2: Edit action**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/prompts/[agent]/actions.ts`:

```typescript
"use server";

import { revalidatePath } from "next/cache";
import { loadInstanceForUser } from "@/lib/get-instance";

export async function savePrompt(
  instanceId: string,
  agent: string,
  formData: FormData
) {
  const prompt = String(formData.get("prompt") ?? "");
  const { client } = await loadInstanceForUser(instanceId);
  await client.writePrompt(agent, prompt);
  revalidatePath(`/instances/${instanceId}/prompts/${agent}`);
}
```

- [ ] **Step 3: Editor page**

Create `/Users/macmini/devrel-swarm/central-app/src/app/instances/[id]/prompts/[agent]/page.tsx`:

```tsx
import { loadInstanceForUser } from "@/lib/get-instance";
import { savePrompt } from "./actions";

export const dynamic = "force-dynamic";

export default async function EditPrompt({
  params,
}: {
  params: Promise<{ id: string; agent: string }>;
}) {
  const { id, agent } = await params;
  const { client } = await loadInstanceForUser(id);
  const { prompt } = await client.readPrompt(agent).catch(() => ({ prompt: "" }));

  const save = savePrompt.bind(null, id, agent);

  return (
    <main className="p-8 max-w-3xl mx-auto space-y-4">
      <h1 className="text-2xl font-semibold">{agent} system prompt</h1>
      <p className="text-sm text-gray-500">
        Saved to <code className="font-mono text-xs">optimize/{agent}/system_prompt.txt</code>.
        Applied on next run — no restart required.
      </p>
      <form action={save} className="space-y-3">
        <textarea
          name="prompt" rows={24} defaultValue={prompt}
          className="block w-full border rounded p-3 font-mono text-sm"
        />
        <button className="px-4 py-2 rounded bg-black text-white text-sm">
          Save
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: Smoke test**

Visit `http://localhost:3000/instances/<id>/prompts/kai`. Edit the prompt, save, refresh, confirm it persisted. Trigger a run; verify Kai picked up the new prompt (check log output or deliverable style).

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/app/instances/[id]/prompts/
git commit -m "feat: prompt editor writing to instance optimize/ via bridge"
```

---

## Phase F — Automated provisioning (Fly Machines)

### Task F.1: Fly Machines API client

**Files:**
- Create: `central-app/src/lib/fly-api.ts`
- Create: `central-app/tests/fly-api.test.ts`

- [ ] **Step 1: Install Vitest for central-app unit tests**

```bash
cd /Users/macmini/devrel-swarm/central-app
pnpm add -D vitest @vitest/ui
```

Add to `package.json` scripts:
```json
"scripts": {
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 2: Write the Fly client**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/fly-api.ts`:

```typescript
/**
 * Thin Fly Machines API client.
 * Docs: https://fly.io/docs/machines/api/
 */

const FLY_API = "https://api.machines.dev/v1";

function headers() {
  const token = process.env.FLY_API_TOKEN;
  if (!token) throw new Error("FLY_API_TOKEN not set");
  return {
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
  };
}

export async function createApp(appName: string, orgSlug: string): Promise<void> {
  const res = await fetch(`${FLY_API}/apps`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ app_name: appName, org_slug: orgSlug }),
  });
  if (!res.ok && res.status !== 409) {
    throw new Error(`fly createApp ${res.status}: ${await res.text()}`);
  }
}

export async function allocateIp(appName: string): Promise<string> {
  const res = await fetch(`${FLY_API}/apps/${appName}/ips`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ type: "shared_v4" }),
  });
  if (!res.ok) throw new Error(`fly allocateIp ${res.status}`);
  return (await res.json()).address as string;
}

export async function createVolume(
  appName: string, name: string, sizeGb: number, region: string
): Promise<{ id: string }> {
  const res = await fetch(`${FLY_API}/apps/${appName}/volumes`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ name, size_gb: sizeGb, region }),
  });
  if (!res.ok) throw new Error(`fly createVolume ${res.status}: ${await res.text()}`);
  return (await res.json()) as { id: string };
}

export async function setSecrets(appName: string, secrets: Record<string, string>): Promise<void> {
  const res = await fetch(`${FLY_API}/apps/${appName}/secrets`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ secrets }),
  });
  if (!res.ok) throw new Error(`fly setSecrets ${res.status}: ${await res.text()}`);
}

export async function createMachine(
  appName: string,
  image: string,
  region: string,
  volumeId: string,
): Promise<{ id: string; private_ip: string }> {
  const res = await fetch(`${FLY_API}/apps/${appName}/machines`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      region,
      config: {
        image,
        env: { INSTANCE_DB_PATH: "/data/instance.db", OPTIMIZE_DIR: "/app/optimize" },
        services: [{
          protocol: "tcp",
          internal_port: 8787,
          ports: [
            { port: 443, handlers: ["tls", "http"] },
            { port: 80, handlers: ["http"] },
          ],
        }],
        mounts: [{ volume: volumeId, path: "/data" }],
        guest: { cpu_kind: "shared", cpus: 1, memory_mb: 1024 },
        auto_destroy: false,
      },
    }),
  });
  if (!res.ok) throw new Error(`fly createMachine ${res.status}: ${await res.text()}`);
  return (await res.json()) as { id: string; private_ip: string };
}
```

- [ ] **Step 3: Basic unit test**

Create `/Users/macmini/devrel-swarm/central-app/tests/fly-api.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";

beforeEach(() => {
  vi.resetModules();
  process.env.FLY_API_TOKEN = "fo_test_123";
});

describe("fly-api", () => {
  it("createApp sends org + name, tolerates 409", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchSpy);
    const { createApp } = await import("../src/lib/fly-api");
    await createApp("openclaw-daria", "personal");
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [, init] = fetchSpy.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      app_name: "openclaw-daria", org_slug: "personal",
    });
  });

  it("createApp swallows 409 Conflict", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("exists", { status: 409 })));
    const { createApp } = await import("../src/lib/fly-api");
    await expect(createApp("x", "y")).resolves.toBeUndefined();
  });

  it("createApp throws on 500", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("boom", { status: 500 })));
    const { createApp } = await import("../src/lib/fly-api");
    await expect(createApp("x", "y")).rejects.toThrow(/500/);
  });
});
```

Run:

```bash
cd /Users/macmini/devrel-swarm/central-app
pnpm test
```

Expected: 3 pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/lib/fly-api.ts central-app/tests/fly-api.test.ts central-app/package.json
git commit -m "feat: Fly Machines API client with unit tests"
```

---

### Task F.2: Provisioning flow — orchestration

**Files:**
- Create: `central-app/src/lib/provision.ts`
- Create: `central-app/src/app/api/instances/provision/route.ts`

- [ ] **Step 1: Orchestration**

Create `/Users/macmini/devrel-swarm/central-app/src/lib/provision.ts`:

```typescript
import crypto from "node:crypto";
import { db, instances, users } from "@/db/client";
import { encryptToken } from "./crypto";
import {
  createApp, allocateIp, createVolume, setSecrets, createMachine,
} from "./fly-api";
import { and, eq } from "drizzle-orm";

const FLY_ORG = process.env.FLY_ORG_SLUG ?? "personal";
const FLY_REGION = process.env.FLY_REGION ?? "ams";
const INSTANCE_IMAGE = process.env.INSTANCE_IMAGE
  ?? "registry.fly.io/devrel-swarm-base:latest";

export async function provisionInstance(params: {
  ownerUserId: string;
  slug: string;
  productName: string;
  anthropicKey: string;
  githubToken: string;
  firecrawlKey?: string;
  braveKey?: string;
  cronSchedule?: string;
}): Promise<{ instanceId: string; url: string }> {
  const appName = `swarm-${params.slug}-${Date.now().toString(36).slice(-4)}`;

  // 1. Create DB row in provisioning state
  const apiToken = crypto.randomBytes(32).toString("hex");
  const [row] = await db.insert(instances).values({
    ownerUserId: params.ownerUserId,
    slug: params.slug,
    flyAppName: appName,
    apiTokenEncrypted: encryptToken(apiToken),
    status: "provisioning",
    productName: params.productName,
    cronSchedule: params.cronSchedule ?? "0 9 * * 1",
  }).returning({ id: instances.id });

  try {
    // 2. Fly app + IP + volume
    await createApp(appName, FLY_ORG);
    await allocateIp(appName);
    const volume = await createVolume(appName, "data", 3, FLY_REGION);

    // 3. Secrets
    await setSecrets(appName, {
      INSTANCE_API_TOKEN: apiToken,
      ANTHROPIC_API_KEY: params.anthropicKey,
      GITHUB_TOKEN: params.githubToken,
      FIRECRAWL_API_KEY: params.firecrawlKey ?? "",
      BRAVE_API_KEY: params.braveKey ?? "",
      CRON_SCHEDULE: params.cronSchedule ?? "0 9 * * 1",
      PRODUCT_NAME: params.productName,
    });

    // 4. Machine
    await createMachine(appName, INSTANCE_IMAGE, FLY_REGION, volume.id);

    // 5. Persist final state
    const url = `https://${appName}.fly.dev`;
    await db.update(instances).set({
      flyAppUrl: url, status: "active", provisionedAt: new Date(),
    }).where(eq(instances.id, row.id));

    return { instanceId: row.id, url };
  } catch (e) {
    await db.update(instances).set({ status: "failed" })
      .where(eq(instances.id, row.id));
    throw e;
  }
}
```

- [ ] **Step 2: Provisioning endpoint**

Create `/Users/macmini/devrel-swarm/central-app/src/app/api/instances/provision/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { db, users } from "@/db/client";
import { provisionInstance } from "@/lib/provision";
import { eq } from "drizzle-orm";

export async function POST(req: Request) {
  const session = await auth();
  if (!session?.user?.email) return NextResponse.json({ error: "unauth" }, { status: 401 });
  const [user] = await db.select().from(users)
    .where(eq(users.email, session.user.email.toLowerCase())).limit(1);
  if (!user) return NextResponse.json({ error: "no user" }, { status: 401 });

  const body = await req.json() as Record<string, string>;
  const result = await provisionInstance({
    ownerUserId: user.id,
    slug: body.slug,
    productName: body.productName,
    anthropicKey: process.env.POOLED_ANTHROPIC_API_KEY!,  // v0: yours
    githubToken: body.githubToken,
    firecrawlKey: process.env.POOLED_FIRECRAWL_API_KEY,
    braveKey: process.env.POOLED_BRAVE_API_KEY,
    cronSchedule: body.cronSchedule,
  });

  return NextResponse.json(result);
}
```

- [ ] **Step 3: Update "new instance" form to offer both modes**

Edit `/Users/macmini/devrel-swarm/central-app/src/app/instances/new/page.tsx` to add a second form (provisioned) alongside the manual one. A simple approach: two sections in the same file, one `<form action={addInstanceManually}>` and one `<form action={provisionAction}>`.

Add a `provisionAction` to the existing `actions.ts` that wraps the API route:

```typescript
export async function provisionInstanceAction(formData: FormData) {
  const session = await auth();
  if (!session?.user?.email) throw new Error("unauth");
  const [user] = await db.select().from(users)
    .where(eq(users.email, session.user.email.toLowerCase())).limit(1);
  if (!user) throw new Error("no user");

  const { provisionInstance } = await import("@/lib/provision");
  const { instanceId } = await provisionInstance({
    ownerUserId: user.id,
    slug: String(formData.get("slug")),
    productName: String(formData.get("productName")),
    anthropicKey: process.env.POOLED_ANTHROPIC_API_KEY!,
    githubToken: String(formData.get("githubToken")),
    cronSchedule: String(formData.get("cronSchedule") || "0 9 * * 1"),
  });
  redirect(`/instances/${instanceId}`);
}
```

- [ ] **Step 4: Prerequisite — push Docker image to Fly registry**

Manual one-time step before using provisioning:

```bash
cd /Users/macmini/devrel-swarm
fly auth login  # if not already
fly apps create devrel-swarm-base --org personal
fly deploy --app devrel-swarm-base --build-only --push --image-label latest
# Or if you prefer a separate builder:
docker build -t registry.fly.io/devrel-swarm-base:latest .
docker push registry.fly.io/devrel-swarm-base:latest
```

Set `INSTANCE_IMAGE=registry.fly.io/devrel-swarm-base:latest` in central-app `.env.local`.

- [ ] **Step 5: E2E provisioning smoke**

From the dashboard, click "+ Add instance", choose "Provision automatically", fill in slug/product/GitHub token. Wait ~90s for the Fly app to boot. Dashboard should show the new instance with status=active and a live URL.

Verify:
```bash
curl -fsS https://swarm-<slug>-<rand>.fly.dev/health
# → {"status":"ok"}
```

- [ ] **Step 6: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add central-app/src/lib/provision.ts central-app/src/app/api/instances/provision/ central-app/src/app/instances/new/
git commit -m "feat: automated Fly Machines provisioning for new instances"
```

---

## Phase G — E2E validation

### Task G.1: Provision the OpenClaw instance via central app

- [ ] **Step 1: Start central app + Postgres**

```bash
cd /Users/macmini/devrel-swarm
docker compose up -d postgres
cd central-app && pnpm dev
```

- [ ] **Step 2: Sign in + provision**

1. Browse `http://localhost:3000`
2. Sign in with GitHub
3. "+ Add instance" → provisioned tab
4. slug=`openclaw`, productName=`OpenClaw`, GitHub token= your PAT, cron=`0 9 * * 1`
5. Wait for status=active

- [ ] **Step 3: Verify**

```bash
URL=$(docker compose exec -T postgres psql -U devrel -d devrel_central -tAc \
  "select fly_app_url from instances where slug='openclaw'")
curl -fsS "$URL/health"
```

Expected: `{"status":"ok"}`.

---

### Task G.2: Full weekly cycle + cost reconciliation

- [ ] **Step 1: Trigger via dashboard**

Open the instance dashboard, click "Run weekly cycle". Wait for completion (~5–10 minutes).

- [ ] **Step 2: Verify deliverables land**

Dashboard → Deliverables tab. Expect at least 1 tutorial + 1 brand_audit row.

- [ ] **Step 3: Verify chat works**

Dashboard → Chat. Prompts:
- "List my deliverables"
- "What's my spend this month?"
- "Read kai's system prompt"

Expect tool calls visible in UI + coherent responses.

- [ ] **Step 4: Verify prompt editor**

Dashboard → Prompts → kai. Add a sentence at the top ("Write with dry humor and one concrete code example"). Save. Run a new cycle. Check that the new tutorial shows the tone shift.

- [ ] **Step 5: Cost reconciliation**

Get tracked total from instance:
```bash
TOKEN=$(docker compose exec -T postgres psql -U devrel -d devrel_central -tAc \
  "select encode(api_token_encrypted, 'hex') from instances where slug='openclaw'")
# (decrypt in Node or just use central app to fetch month_cost_cents)
```

Easier: in the instance dashboard UI, the header shows month spend. Compare that value to `https://console.anthropic.com/dashboard` → Usage filtered to the same time window.

**Acceptance:** tracked cents within ±5% of Anthropic console.

If drift > 5%:
- Verify every LLM code path in `agents/llm.py` calls `_emit_cost()`
- Verify `_PRICING` table in `workers/budget.py` matches current Anthropic pricing
- Verify cache_creation + cache_read tokens are being read from response

- [ ] **Step 6: Tag v0.1-agentic-alpha**

```bash
cd /Users/macmini/devrel-swarm
git tag -a v0.1-agentic-alpha -m "v0 agentic alpha: OpenClaw instance provisioned via central app, dashboard + chat + prompt editor operational"
```

---

## v0 Exit gate checklist

- [ ] `pytest tests/ -v` green
- [ ] `cd central-app && pnpm test` green
- [ ] `docker build .` succeeds; image runs and `/health` returns `ok`
- [ ] OpenClaw instance provisioned via central-app UI (not manual SQL)
- [ ] One full weekly cycle completes via dashboard "Run now" button
- [ ] Dashboard shows ≥1 tutorial + ≥1 brand_audit deliverable
- [ ] Chat can list deliverables + trigger a run + read/write a prompt
- [ ] Prompt editor round-trip: edit → save → next run reflects the change
- [ ] Tracked cost within ±5% of Anthropic console for the run's window
- [ ] Atlas CLI back-compat still works: `python -m agents.atlas --help`

---

## Self-review

**Spec coverage:**
- Revised §2b architecture → fully covered by Phases A+B+C+D+E+F
- Revised §6 v0 gate criteria → Phase G verification script + exit checklist
- Agent roster §3 MVP agents — 8 existing kept, new agents (Publisher, Seed, Mimic, Meter) explicitly listed as v1 in scope section
- Optimization plan §4 — prompt editor fulfills "extend optimization beyond emails" for v0 (manual editing; autoresearch loop is v1.1)
- User journey §5 — simplified to "sign in → add instance → run → review → chat/tune" for v0 solo use; full onboarding wizard is v1

**Placeholder scan:** no TODOs left. Task F.1 Step 4 requires manual Fly auth — documented as explicit prerequisite, not a placeholder.

**Type consistency:**
- `CostRecord` fields (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) used identically across `budget.py`, `llm.py` `_emit_cost`, `run_dispatch.py` `cost_sink`
- `Deliverable` fields (`kind`, `title`, `body_md`, `quality_score`, `voice_score`) used identically in `storage.py`, `atlas.py` sinks, `instance-client.ts`, dashboard pages
- `Job` fields (`id`, `kind`, `status`, `cost_cents`, `created_at`) used identically in SQLite, bridge response, `InstanceClient`, dashboard
- HTTP bridge endpoints referenced from `instance-client.ts` all defined in `tools/http_bridge.py`

**Scope check:** plan produces a working single-instance product (OpenClaw provisioned through the central app) — runnable + testable on its own. v1 (billing, multi-tenant onboarding, new agents) gets its own plan.

---

## Notes for the executor

- **Existing envs:** preserve `.env` (used by workers/instance), add new `central-app/.env.local` for central app
- **Fly costs:** one `shared-cpu-1x@1024MB` machine ≈ $2.50/mo + 3GB volume ≈ $0.45/mo. Alpha footprint for OpenClaw: ~$3/mo.
- **Model defaults:** instance keeps existing Sonnet 4.6 default (`config/agent_config.yaml`). Model routing (Haiku/Sonnet/Opus by task) is a v1 optimization.
- **KB content:** existing markdown in `knowledge_base/` ships inside the image. Per-customer KB harvesting lives in v1 (tools/kb_harvester.py is already in repo, not wired to bridge in v0).
- **Rolling updates:** when you push a new Docker image, existing instances keep running the old one until you run `fly machines update` per app. Automated rollout is v1.
- **Per-run cost budget:** Sonnet 4.6 weekly cycle ≈ $3–8 during alpha. OpenClaw monthly cap of $100 gives 12+ runs of headroom.
