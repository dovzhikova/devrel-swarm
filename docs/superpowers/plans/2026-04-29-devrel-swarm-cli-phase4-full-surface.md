# devrel-swarm CLI — Phase 4: Full CLI Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the remaining ~25 CLI verbs around the existing agent surface, plus a cost-tracking hook so `devrel cost` has data, plus minor `Atlas` extensions for the pipeline-subset commands. After Phase 4, the CLI matches the §3 surface in the spec end-to-end.

**Architecture:** Most verbs are thin wrappers around `Atlas.run_single_task(agent_name, task)` — they reuse the existing agent code unchanged. The cost hook (salvaged from the abandoned `v0-agentic-alpha` branch) lets the LLMClient emit a cost event after each Anthropic response; Atlas registers a sink that writes rows into `.devrel/state.db`'s `costs` table, where `devrel cost` then aggregates them. KB/schedule verbs wrap existing tools modules. Config/deliverables/content-slop verbs are pure file-system ops.

**Tech Stack:** Typer + Rich (existing), `tomli-w` (already a dep) for config writes, stdlib `sqlite3` for cost queries.

**Spec:** `docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md`
**Phases 1-3 (prerequisites, all merged):** `be971bd`, `121187e`, `bfb3bb5` on `main`.

---

## File structure after Phase 4

```
src/devrel_swarm/
  cli/
    run.py                NEW   `devrel run` (full weekly + --health + --agent)
    triage.py             NEW   `devrel triage`
    listen.py             NEW   `devrel listen`
    synthesize.py         NEW   `devrel synthesize`
    experiment.py         NEW   `devrel experiment`
    intel.py              NEW   `devrel intel`
    sales.py              NEW   `devrel sales {outreach,battlecard,sequence}`
    marketing.py          NEW   `devrel marketing {blog,landing,social,campaign}`
    kb.py                 NEW   `devrel kb {add,list,refresh}`
    schedule.py           NEW   `devrel schedule {install,list,remove}`
    cost.py               NEW   `devrel cost`
    deliverables.py       NEW   `devrel deliverables {list,show}`
    config.py             NEW   `devrel config {get,set}`
    docs.py               NEW   `devrel docs build`
    video.py              NEW   `devrel video record`
    content.py            MODIFY add `slop` subcommand
    _common.py            NEW   shared helpers: build_atlas, format_result, find_paths_or_exit
  core/
    llm.py                MODIFY add set_cost_sink + _emit_cost (salvaged from v0)
    atlas.py              MODIFY register cost sink in __init__ when project_paths given
tests/
  cli/                    NEW tests for every new verb (one file per verb module)
  core/test_llm_cost_sink.py  NEW
```

No changes to agent modules. No new dependencies. The new verbs reuse `Atlas.delegate()` / `Atlas.run_weekly_cycle()` exclusively.

---

## Pre-flight: worktree setup

- [ ] **Step 1: Create a fresh worktree off `main`**

Use **superpowers:using-git-worktrees** to create `.worktrees/cli-phase4-surface` on a new branch `feat/cli-phase4-surface`. Confirm `main` is at `bfb3bb5` or later.

- [ ] **Step 2: Confirm starting state + capture baseline**

```bash
source .venv/bin/activate || /opt/homebrew/bin/python3.13 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]' >/tmp/install.preflight.log 2>&1 && echo "exit=$?"
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort > /tmp/pytest.failures.phase4.before.txt
wc -l /tmp/pytest.failures.phase4.before.txt
```
Expected: `exit=0`, `653 passed, 22 failed`, `22` lines.

---

## Task 1: Cost hook — wire LLMClient → state.db `costs` table

This is the foundation for `devrel cost`. We salvage the `set_cost_sink` / `_emit_cost` mechanism from the abandoned v0-agentic-alpha branch (commit `0b16a90`) and wire Atlas to register a sink that persists rows.

**Files:**
- Modify: `src/devrel_swarm/core/llm.py` — add `set_cost_sink` + `_emit_cost`, call `_emit_cost` after each successful Anthropic response.
- Modify: `src/devrel_swarm/core/atlas.py` — when `Atlas` is constructed with knowledge of a project path, register a sink that writes to `state.db`.
- Create: `tests/core/__init__.py`
- Create: `tests/core/test_llm_cost_sink.py`
- Create: `src/devrel_swarm/quality/_cost_sink.py` — adapter (pure helper, no LLM dep) that takes a state-db path and returns an async callable `(agent, model, usage) -> None` that inserts a `costs` row.

  Actually, putting the cost-sink helper under `quality/` is wrong (it has nothing to do with the quality pipeline). Place it under `src/devrel_swarm/project/cost_sink.py` instead — it's project-DB-aware infrastructure.

- Create: `src/devrel_swarm/project/cost_sink.py`
- Create: `tests/project/test_cost_sink.py`

Pricing for cost-USD computation lives in `core/llm.py`'s `MODEL_COSTS` dict (already defined). The sink converts token counts × pricing into a `cost_usd` per call.

- [ ] **Step 1: Add `set_cost_sink` + `_emit_cost` to `llm.py`**

In `src/devrel_swarm/core/llm.py`, find the `LLMClient.__init__` method and add this initialization:
```python
        self._cost_sink: "Callable[[str, str, dict[str, Any]], Awaitable[None]] | None" = None
```
Add the imports:
```python
from collections.abc import Awaitable
from typing import Callable
```
(or add `Callable, Awaitable` to the existing `from typing import Any` line as appropriate.)

After the `set_agent` method, add:
```python
    def set_cost_sink(
        self,
        sink: "Callable[[str, str, dict[str, Any]], Awaitable[None]] | None",
    ) -> None:
        """Register async callback ``(agent, model, usage_dict) -> None``.

        Called once per successful Anthropic API response. ``None`` clears
        the sink. Sink exceptions are caught and logged at WARNING — they
        never break the LLM call (cost recording is best-effort).
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
        try:
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
        except Exception as e:
            logger.warning("cost sink raised; ignoring: %s", e)
```

In the `generate` method, after the `response = await self._client.messages.create(...)` call and after `_check_budget()`, add:
```python
        await self._emit_cost(
            model=resolved_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=getattr(
                response.usage, "cache_creation_input_tokens", 0
            ) or 0,
            cache_read_input_tokens=getattr(
                response.usage, "cache_read_input_tokens", 0
            ) or 0,
        )
```

The exact placement is right after `_check_budget()` and before the existing `logger.info("llm_call", extra=...)` call.

- [ ] **Step 2: Write the cost-sink unit test**

Create `tests/core/__init__.py` (empty).

Create `tests/core/test_llm_cost_sink.py`:
```python
"""Tests for LLMClient.set_cost_sink + _emit_cost."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.llm import LLMClient


@pytest.mark.asyncio
async def test_emit_cost_calls_sink_with_agent_and_model():
    client = LLMClient(api_key="dummy")
    sink = AsyncMock()
    client.set_cost_sink(sink)
    client.set_agent("kai")

    await client._emit_cost(
        model="claude-sonnet-4-5-20250929",
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=10,
        cache_read_input_tokens=5,
    )
    sink.assert_awaited_once()
    args = sink.await_args.args
    assert args[0] == "kai"
    assert args[1] == "claude-sonnet-4-5-20250929"
    assert args[2] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    }


@pytest.mark.asyncio
async def test_emit_cost_noop_without_sink():
    client = LLMClient(api_key="dummy")
    # No sink registered. Should not raise, should not log error.
    await client._emit_cost(
        model="claude-haiku-4-5-20251001",
        input_tokens=10,
        output_tokens=5,
    )


@pytest.mark.asyncio
async def test_emit_cost_uses_unknown_when_no_agent_set():
    client = LLMClient(api_key="dummy")
    sink = AsyncMock()
    client.set_cost_sink(sink)
    # Don't call set_agent.
    await client._emit_cost(model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1)
    assert sink.await_args.args[0] == "unknown"


@pytest.mark.asyncio
async def test_set_cost_sink_to_none_clears():
    client = LLMClient(api_key="dummy")
    sink = AsyncMock()
    client.set_cost_sink(sink)
    client.set_cost_sink(None)
    await client._emit_cost(model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1)
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_exception_does_not_break_emit():
    client = LLMClient(api_key="dummy")
    sink = AsyncMock(side_effect=RuntimeError("DB unreachable"))
    client.set_cost_sink(sink)
    client.set_agent("sage")
    # Must not raise.
    await client._emit_cost(model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1)
    sink.assert_awaited_once()
```

- [ ] **Step 3: Run cost-sink test, verify pass**

```bash
python -m pytest tests/core/test_llm_cost_sink.py -v --no-cov 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 4: Implement the SQLite cost-sink adapter**

Create `src/devrel_swarm/project/cost_sink.py`:
```python
"""Build a cost-sink callable that inserts rows into the project state DB.

Used by Atlas to wire LLMClient cost events into `.devrel/state.db`'s
`costs` table. The pricing table lives in core/llm.py — we read it
indirectly via the model names we receive.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from devrel_swarm.core.llm import MODEL_COSTS


def _compute_cost_usd(model: str, usage: dict[str, Any]) -> float:
    pricing = MODEL_COSTS.get(model)
    if pricing is None:
        return 0.0
    input_per_1m = pricing["input"]
    output_per_1m = pricing["output"]
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    # Cache pricing: read at 0.1×, write at 1.25× of input rate (Anthropic standard)
    cost = (
        (input_tokens / 1_000_000) * input_per_1m
        + (output_tokens / 1_000_000) * output_per_1m
        + (cache_read / 1_000_000) * input_per_1m * 0.1
        + (cache_write / 1_000_000) * input_per_1m * 1.25
    )
    return round(cost, 6)


def make_sqlite_sink(db_path: Path):
    """Return an async ``(agent, model, usage) -> None`` callback that inserts
    a row into the `costs` table at `db_path`."""

    async def _sink(agent: str, model: str, usage: dict[str, Any]) -> None:
        cost_usd = _compute_cost_usd(model, usage)
        # SQLite is sync; we accept the brief blocking write inline.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO costs (agent, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    agent,
                    model,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                    usage.get("cache_creation_input_tokens", 0),
                    cost_usd,
                ),
            )
            conn.commit()

    return _sink
```

- [ ] **Step 5: Write tests for the SQLite sink**

Create `tests/project/test_cost_sink.py`:
```python
"""Tests for the SQLite cost-sink adapter."""

from __future__ import annotations

import sqlite3

import pytest

from devrel_swarm.project.cost_sink import _compute_cost_usd, make_sqlite_sink
from devrel_swarm.project.state import init_db


def test_compute_cost_usd_sonnet():
    # Sonnet 4.5: $3 input / $15 output per 1M
    cost = _compute_cost_usd(
        "claude-sonnet-4-5-20250929",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    assert cost == pytest.approx(3.0)
    cost = _compute_cost_usd(
        "claude-sonnet-4-5-20250929",
        {"input_tokens": 0, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(15.0)


def test_compute_cost_usd_unknown_model_returns_zero():
    assert _compute_cost_usd("not-a-real-model", {"input_tokens": 1000}) == 0.0


def test_compute_cost_usd_includes_cache_tokens():
    base = _compute_cost_usd(
        "claude-haiku-4-5-20251001",
        {"input_tokens": 0, "output_tokens": 0},
    )
    with_cache = _compute_cost_usd(
        "claude-haiku-4-5-20251001",
        {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
        },
    )
    assert with_cache > base


@pytest.mark.asyncio
async def test_sqlite_sink_inserts_row(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    sink = make_sqlite_sink(db)

    await sink(
        "kai",
        "claude-sonnet-4-5-20250929",
        {
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        },
    )

    with sqlite3.connect(db) as conn:
        rows = list(conn.execute("SELECT agent, model, input_tokens, output_tokens, cost_usd FROM costs"))
    assert len(rows) == 1
    agent, model, in_t, out_t, cost = rows[0]
    assert agent == "kai"
    assert in_t == 100
    assert out_t == 50
    assert cost > 0


@pytest.mark.asyncio
async def test_sqlite_sink_two_calls_two_rows(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    sink = make_sqlite_sink(db)
    for agent in ("sage", "kai"):
        await sink(
            agent, "claude-haiku-4-5-20251001",
            {"input_tokens": 10, "output_tokens": 5},
        )
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
    assert n == 2
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/project/test_cost_sink.py tests/core/test_llm_cost_sink.py -v --no-cov 2>&1 | tail -10
```
Expected: 10 passed (5 + 5).

- [ ] **Step 7: Wire Atlas to register the sink when project paths are known**

In `src/devrel_swarm/core/atlas.py`, find `Atlas.__init__`. Currently it constructs LLMClient unconditionally. Add (after the LLMClient is constructed):
```python
        # If the caller passed a project_paths, wire cost events into state.db.
        from devrel_swarm.project.paths import ProjectPaths
        project_paths = kwargs.get("project_paths") if isinstance(kwargs, dict) else None
        if isinstance(project_paths, ProjectPaths) and project_paths.state_db.is_file():
            from devrel_swarm.project.cost_sink import make_sqlite_sink
            self.llm_client.set_cost_sink(make_sqlite_sink(project_paths.state_db))
```

If `Atlas.__init__`'s signature doesn't accept `**kwargs`, extend it to take an optional `project_paths: ProjectPaths | None = None` parameter. Either way, the wire-up is gated on the path existing.

- [ ] **Step 8: Run full suite**

```bash
python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
diff /tmp/pytest.failures.phase4.before.txt <(python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | grep "^FAILED" | sort)
```
Expected: `663 passed, 22 failed` (653 + 10 new). Diff empty.

- [ ] **Step 9: Commit**

```bash
git add src/devrel_swarm/core/llm.py src/devrel_swarm/core/atlas.py \
        src/devrel_swarm/project/cost_sink.py \
        tests/core/__init__.py tests/core/test_llm_cost_sink.py \
        tests/project/test_cost_sink.py
git commit -m "feat(cost): add LLMClient cost sink + SQLite adapter; wire Atlas"
```

---

## Common pattern (used by Tasks 2-9)

Most Phase 4 commands wrap a single agent invocation. The shared pattern:

```python
# In a per-verb file like src/devrel_swarm/cli/<verb>.py:

from __future__ import annotations

import asyncio
import os

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def <verb>_command(
    <verb-specific-options>,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """<one-line description>"""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("<agent_name>", <task_string>)
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

Define the shared helpers ONCE in `src/devrel_swarm/cli/_common.py`:

```python
"""Shared CLI helpers."""

from __future__ import annotations

import json
import os
import sys

import typer
from rich.console import Console

from devrel_swarm.core.atlas import Atlas, DelegationResult
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectNotFoundError, ProjectPaths, find_devrel_root


def find_paths_or_exit(console: Console) -> ProjectPaths:
    try:
        return ProjectPaths.from_root(find_devrel_root())
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


def build_atlas_or_exit(paths: ProjectPaths, console: Console) -> Atlas:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY is required.[/red]")
        raise typer.Exit(code=1)
    llm = LLMClient(api_key=api_key)
    try:
        return Atlas(llm_client=llm, project_paths=paths)
    except TypeError:
        # Atlas may not yet accept project_paths kwarg.
        return Atlas(llm_client=llm)


def render_result(
    result: DelegationResult, console: Console, *, json_output: bool = False
) -> None:
    if json_output:
        # DelegationResult is a dataclass; convert via dict()/asdict.
        from dataclasses import asdict
        try:
            payload = asdict(result)
        except TypeError:
            payload = {
                "agent": getattr(result, "agent", "?"),
                "task": getattr(result, "task", "?"),
                "success": getattr(result, "success", False),
                "result": getattr(result, "result", None),
                "error": getattr(result, "error", None),
            }
        typer.echo(json.dumps(payload, default=str, indent=2))
        return
    if not result.success:
        console.print(f"[red]✗[/red] {result.agent} failed: {result.error}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] {result.agent} completed")
    if isinstance(result.result, dict):
        for k, v in list(result.result.items())[:8]:
            console.print(f"  [dim]{k}:[/dim] {str(v)[:120]}")
    elif result.result:
        console.print(f"  {str(result.result)[:300]}")
```

Tasks 2-9 reuse these helpers. Each verb file is ~30-50 lines.

A test pattern (also reused) — one test per verb that mocks `Atlas.run_single_task`:

```python
# tests/cli/test_<verb>_command.py
from unittest.mock import AsyncMock, MagicMock, patch
from typer.testing import CliRunner
from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def test_<verb>_dispatches_to_<agent>(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            instance = MockAtlas.return_value
            instance.run_single_task = AsyncMock(return_value=MagicMock(
                success=True, agent="<agent>", result={"summary": "ok"},
            ))
            result = runner.invoke(app, ["<verb>", <args>], env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        instance.run_single_task.assert_awaited_once()
        called_agent, called_task = instance.run_single_task.await_args.args
        assert called_agent == "<agent>"
    finally:
        os.chdir(cwd)
```

Each verb-task in this plan instantiates this pattern with its own `<verb>`, `<agent>`, and any verb-specific argument handling.

---

## Task 2: `devrel run` (full pipeline + --health + --agent)

**Files:**
- Create: `src/devrel_swarm/cli/_common.py` (the shared helper module above)
- Create: `src/devrel_swarm/cli/run.py`
- Create: `tests/cli/test_run_command.py`
- Modify: `src/devrel_swarm/cli/__init__.py` (register the run command)

`devrel run` (no args) calls `Atlas.run_weekly_cycle()`. `devrel run --health` runs only Watchdog. `devrel run --agent <name> [--task <task>]` runs a single agent.

- [ ] **Step 1: Create `cli/_common.py`** with the helper code shown in the "Common pattern" section above. Verbatim.

- [ ] **Step 2: Create `cli/run.py`**

```python
"""`devrel run` — full weekly pipeline, health check, or single agent."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit

console = Console()


def run_command(
    health: bool = typer.Option(False, "--health", help="Only run the Watchdog health check."),
    agent: str = typer.Option("", "--agent", help="Run a single agent by name (e.g., 'kai')."),
    task: str = typer.Option("", "--task", help="Task description for --agent."),
) -> None:
    """Run the full weekly pipeline (default), or a subset via flags."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        if health:
            result = await atlas.run_single_task("watchdog", "Check system health")
            console.print(f"[green]✓[/green] watchdog: {str(result.result)[:300]}" if result.success else f"[red]✗[/red] {result.error}")
            return
        if agent:
            t = task or f"Run {agent} with default settings"
            result = await atlas.run_single_task(agent, t)
            if result.success:
                console.print(f"[green]✓[/green] {agent}: {str(result.result)[:300]}")
            else:
                console.print(f"[red]✗[/red] {agent} failed: {result.error}")
                raise typer.Exit(code=1)
            return
        # Full weekly pipeline.
        ctx = await atlas.run_weekly_cycle()
        console.print(f"[bold green]Weekly cycle complete.[/bold green] week_of={ctx.week_of}")

    asyncio.run(_do())
```

- [ ] **Step 3: Register `run` in `cli/__init__.py`**

In `src/devrel_swarm/cli/__init__.py`, add:
```python
from devrel_swarm.cli.run import run_command
```
and:
```python
app.command(name="run")(run_command)
```

- [ ] **Step 4: Write `tests/cli/test_run_command.py`**

```python
"""Tests for `devrel run`."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def test_run_health_calls_watchdog(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_single_task = AsyncMock(return_value=MagicMock(success=True, agent="watchdog", result={"checks": "ok"}, error=None))
            result = runner.invoke(app, ["run", "--health"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        inst.run_single_task.assert_awaited_once_with("watchdog", "Check system health")
    finally:
        os.chdir(cwd)


def test_run_agent_dispatches_named_agent(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_single_task = AsyncMock(return_value=MagicMock(success=True, agent="kai", result="ok", error=None))
            result = runner.invoke(app, ["run", "--agent", "kai", "--task", "Write tutorial"],
                                   env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        called_agent, called_task = inst.run_single_task.await_args.args
        assert called_agent == "kai"
        assert called_task == "Write tutorial"
    finally:
        os.chdir(cwd)


def test_run_default_calls_weekly_cycle(tmp_path):
    _init(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with patch("devrel_swarm.cli._common.Atlas") as MockAtlas:
            inst = MockAtlas.return_value
            inst.run_weekly_cycle = AsyncMock(return_value=MagicMock(week_of="2026-W18"))
            result = runner.invoke(app, ["run"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
        assert result.exit_code == 0, result.output
        inst.run_weekly_cycle.assert_awaited_once()
    finally:
        os.chdir(cwd)


def test_run_fails_without_devrel_dir(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["run"], env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)
    assert result.exit_code != 0
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/cli/test_run_command.py -v --no-cov 2>&1 | tail -10
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_swarm/cli/_common.py src/devrel_swarm/cli/run.py \
        src/devrel_swarm/cli/__init__.py tests/cli/test_run_command.py
git commit -m "feat(cli): add 'devrel run' (full weekly + --health + --agent)"
```

---

## Task 3: DevRel verbs — triage, listen, synthesize, experiment

These four follow the canonical pattern verbatim. Single dispatch since they're identical in shape.

**Files:**
- Create: `src/devrel_swarm/cli/triage.py`, `listen.py`, `synthesize.py`, `experiment.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_triage_command.py`, `test_listen_command.py`, `test_synthesize_command.py`, `test_experiment_command.py`

**Per-verb specs:**

| Verb | Agent | Task string | Extra options |
|------|-------|-------------|---------------|
| `triage` | `sage` | `f"Triage GitHub issues from the last {days} days"` | `--days INT (default 7)` |
| `listen` | `echo` | `f"Scan {platforms} for mentions"` | `--platforms STR (default "reddit,hn,twitter")` |
| `synthesize` | `iris` | `"Extract themes from latest social + triage signals"` | none |
| `experiment` | `nova` | `f"Design experiment for hypothesis: {hypothesis}"` | `hypothesis: positional STR` |

- [ ] **Step 1: Create `cli/triage.py`**

```python
"""`devrel triage` — GitHub issue triage via Sage."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def triage_command(
    days: int = typer.Option(7, "--days", help="Look back this many days."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Triage GitHub issues from the last N days."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "sage", f"Triage GitHub issues from the last {days} days"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 2: Create `cli/listen.py`**

```python
"""`devrel listen` — social-media listening via Echo."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def listen_command(
    platforms: str = typer.Option(
        "reddit,hn,twitter", "--platforms",
        help="Comma-separated platforms to scan.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan social media for product mentions and sentiment."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "echo", f"Scan {platforms} for product mentions"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 3: Create `cli/synthesize.py`**

```python
"""`devrel synthesize` — theme extraction via Iris."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def synthesize_command(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Extract themes from latest social + triage signals via Iris."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "iris", "Extract themes from latest social + triage signals"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 4: Create `cli/experiment.py`**

```python
"""`devrel experiment` — A/B experiment design via Nova."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def experiment_command(
    hypothesis: str = typer.Argument(..., help="The hypothesis to test."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Design an A/B experiment with power analysis via Nova."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "nova", f"Design experiment for hypothesis: {hypothesis}"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 5: Register all four in `cli/__init__.py`**

```python
from devrel_swarm.cli.triage import triage_command
from devrel_swarm.cli.listen import listen_command
from devrel_swarm.cli.synthesize import synthesize_command
from devrel_swarm.cli.experiment import experiment_command
# ... after existing registrations:
app.command(name="triage")(triage_command)
app.command(name="listen")(listen_command)
app.command(name="synthesize")(synthesize_command)
app.command(name="experiment")(experiment_command)
```

- [ ] **Step 6: Write a single test file covering all four**

Create `tests/cli/test_devrel_verbs.py`:
```python
"""Tests for devrel-pipeline verbs (triage, listen, synthesize, experiment)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


@pytest.fixture
def mock_atlas():
    with patch("devrel_swarm.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(return_value=MagicMock(
            success=True, agent="?", result={"summary": "ok"}, error=None,
        ))
        yield M, inst


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_triage_dispatches_to_sage(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["triage", "--days", "3"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "sage"
    assert "3 days" in task


def test_listen_dispatches_to_echo(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["listen", "--platforms", "reddit"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "echo"
    assert "reddit" in task


def test_synthesize_dispatches_to_iris(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["synthesize"])
    assert result.exit_code == 0, result.output
    agent, _ = inst.run_single_task.await_args.args
    assert agent == "iris"


def test_experiment_dispatches_to_nova(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    result = _run(tmp_path, ["experiment", "Bigger CTA increases signups"])
    assert result.exit_code == 0, result.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "nova"
    assert "Bigger CTA" in task


def test_json_output_emits_valid_json(tmp_path, mock_atlas):
    _init(tmp_path)
    result = _run(tmp_path, ["triage", "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert data["agent"] == "?"  # mock returned this
    assert data["success"] is True
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/cli/test_devrel_verbs.py -v --no-cov 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 8: Commit**

```bash
git add src/devrel_swarm/cli/triage.py src/devrel_swarm/cli/listen.py \
        src/devrel_swarm/cli/synthesize.py src/devrel_swarm/cli/experiment.py \
        src/devrel_swarm/cli/__init__.py tests/cli/test_devrel_verbs.py
git commit -m "feat(cli): add devrel verbs (triage, listen, synthesize, experiment)"
```

---

## Task 4: Sales verbs — intel, sales {outreach, battlecard, sequence}

Same pattern. `sales` is a Typer subapp with three subcommands; `intel` is a top-level verb.

**Files:**
- Create: `src/devrel_swarm/cli/intel.py`
- Create: `src/devrel_swarm/cli/sales.py` (Typer subapp with 3 subcommands)
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_sales_verbs.py`

**Specs:**

| Verb | Agent | Task | Args |
|------|-------|------|------|
| `intel` | `rex` | `f"Compile competitive intel on {competitor}"` | `competitor: positional` |
| `sales outreach` | `pax` | `f"Draft outreach email for {company}"` | `company: positional` |
| `sales battlecard` | `pax` | `f"Build battle card vs. {competitor}"` | `competitor: positional` |
| `sales sequence` | `pax` | `f"Design nurture sequence: {campaign}"` | `campaign: positional` |

- [ ] **Step 1: Create `cli/intel.py`**

```python
"""`devrel intel` — competitive intelligence via Rex."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def intel_command(
    competitor: str = typer.Argument(..., help="Competitor name."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Gather competitive intel on a named competitor."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "rex", f"Compile competitive intel on {competitor}"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 2: Create `cli/sales.py`** (subapp)

```python
"""`devrel sales {outreach, battlecard, sequence}` — Pax-powered sales surfaces."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

sales_app = typer.Typer(
    name="sales",
    help="Sales enablement: outreach, battle cards, nurture sequences.",
    no_args_is_help=True,
    add_completion=False,
)


def _run(task: str, json_output: bool) -> None:
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("pax", task)
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())


@sales_app.command("outreach")
def outreach(
    company: str = typer.Argument(..., help="Target company."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Draft a cold outreach email for a target company."""
    _run(f"Draft outreach email for {company}", json_output)


@sales_app.command("battlecard")
def battlecard(
    competitor: str = typer.Argument(..., help="Competitor."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build a sales battle card against a competitor."""
    _run(f"Build battle card vs. {competitor}", json_output)


@sales_app.command("sequence")
def sequence(
    campaign: str = typer.Argument(..., help="Campaign description."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Design a multi-touch nurture sequence."""
    _run(f"Design nurture sequence: {campaign}", json_output)
```

- [ ] **Step 3: Register both in `cli/__init__.py`**

```python
from devrel_swarm.cli.intel import intel_command
from devrel_swarm.cli.sales import sales_app
# ...
app.command(name="intel")(intel_command)
app.add_typer(sales_app, name="sales")
```

- [ ] **Step 4: Write tests**

Create `tests/cli/test_sales_verbs.py` (4 tests, mirror the devrel-verbs test file structure with the same `_init` and `_run` helpers, asserting agent + task content for each verb).

```python
"""Tests for intel + sales subcommands."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


@pytest.fixture
def mock_atlas():
    with patch("devrel_swarm.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(return_value=MagicMock(
            success=True, agent="?", result={"k": "v"}, error=None,
        ))
        yield M, inst


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_intel_dispatches_to_rex(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["intel", "AcmeCorp"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "rex"
    assert "AcmeCorp" in task


def test_sales_outreach_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "outreach", "Globex"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "Globex" in task and "outreach" in task.lower()


def test_sales_battlecard_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "battlecard", "Acme"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "battle card" in task.lower() and "Acme" in task


def test_sales_sequence_dispatches_to_pax(tmp_path, mock_atlas):
    _init(tmp_path)
    _, inst = mock_atlas
    r = _run(tmp_path, ["sales", "sequence", "Q3 launch"])
    assert r.exit_code == 0, r.output
    agent, task = inst.run_single_task.await_args.args
    assert agent == "pax"
    assert "Q3 launch" in task
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/cli/test_sales_verbs.py -v --no-cov 2>&1 | tail -10
```
Expected: 4 passed.

```bash
git add src/devrel_swarm/cli/intel.py src/devrel_swarm/cli/sales.py \
        src/devrel_swarm/cli/__init__.py tests/cli/test_sales_verbs.py
git commit -m "feat(cli): add 'devrel intel' + 'devrel sales {outreach|battlecard|sequence}'"
```

---

## Task 5: Marketing verbs — `marketing {blog, landing, social, campaign}`

Same pattern as sales. Subapp + 4 subcommands.

**Files:**
- Create: `src/devrel_swarm/cli/marketing.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_marketing_verbs.py`

**Specs:**

| Verb | Agent | Task |
|------|-------|------|
| `marketing blog` | `mox` | `f"Write blog post: {topic}"` |
| `marketing landing` | `mox` | `f"Write landing page copy: {topic}"` |
| `marketing social` | `mox` | `f"Write social batch: {topic}"` |
| `marketing campaign` | `mox` | `f"Build campaign: {brief}"` |

- [ ] **Step 1: Create `cli/marketing.py`**

```python
"""`devrel marketing {blog, landing, social, campaign}` — Mox-powered surfaces."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

marketing_app = typer.Typer(
    name="marketing",
    help="Marketing campaigns: blog posts, landing pages, social, full campaigns.",
    no_args_is_help=True,
    add_completion=False,
)


def _run(task: str, json_output: bool) -> None:
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("mox", task)
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())


@marketing_app.command("blog")
def blog(
    topic: str = typer.Argument(..., help="Blog topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write a blog post on a topic."""
    _run(f"Write blog post: {topic}", json_output)


@marketing_app.command("landing")
def landing(
    topic: str = typer.Argument(..., help="Landing page topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write landing page copy."""
    _run(f"Write landing page copy: {topic}", json_output)


@marketing_app.command("social")
def social(
    topic: str = typer.Argument(..., help="Social topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write a social media batch."""
    _run(f"Write social batch: {topic}", json_output)


@marketing_app.command("campaign")
def campaign(
    brief: str = typer.Argument(..., help="Campaign brief."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build a full marketing campaign."""
    _run(f"Build campaign: {brief}", json_output)
```

- [ ] **Step 2: Register in `cli/__init__.py`**

```python
from devrel_swarm.cli.marketing import marketing_app
# ...
app.add_typer(marketing_app, name="marketing")
```

- [ ] **Step 3: Write tests**

Create `tests/cli/test_marketing_verbs.py` — 4 tests mirroring the sales-verbs structure, asserting `mox` is called with the right task strings.

(Use the same `_init`/`_run`/`mock_atlas` boilerplate. Assert `agent == "mox"` for each.)

- [ ] **Step 4: Run + commit**

```bash
python -m pytest tests/cli/test_marketing_verbs.py -v --no-cov 2>&1 | tail -10
```
Expected: 4 passed.

```bash
git add src/devrel_swarm/cli/marketing.py src/devrel_swarm/cli/__init__.py \
        tests/cli/test_marketing_verbs.py
git commit -m "feat(cli): add 'devrel marketing {blog|landing|social|campaign}'"
```

---

## Task 6: KB and Schedule verbs

These wrap existing tools (`tools/kb_harvester.py`, `tools/scheduler.py`) instead of agents.

**Files:**
- Create: `src/devrel_swarm/cli/kb.py`
- Create: `src/devrel_swarm/cli/schedule.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_kb_verbs.py`, `tests/cli/test_schedule_verbs.py`

- [ ] **Step 1: Create `cli/kb.py`**

`devrel kb add <url>` runs `tools.kb_harvester.harvest_url()`. `devrel kb list` lists files in `.devrel/kb/`. `devrel kb refresh` runs `harvest_all()`.

```python
"""`devrel kb {add, list, refresh}` — knowledge base management."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.tools.kb_harvester import KBHarvester

console = Console()

kb_app = typer.Typer(
    name="kb",
    help="Knowledge base management.",
    no_args_is_help=True,
    add_completion=False,
)


@kb_app.command("add")
def add(
    url: str = typer.Argument(..., help="URL to harvest into the KB."),
    category: str = typer.Option("docs", "--category", help="KB subdirectory to write into."),
) -> None:
    """Harvest a URL into the project KB."""
    paths = find_paths_or_exit(console)
    harvester = KBHarvester(kb_root=paths.kb_dir)

    async def _do() -> None:
        result = await harvester.harvest_url(url, category=category)
        if result:
            console.print(f"[green]✓[/green] Harvested {url} → {category}/")
        else:
            console.print(f"[red]✗[/red] Failed to harvest {url}")
            raise typer.Exit(code=1)

    asyncio.run(_do())


@kb_app.command("list")
def list_kb() -> None:
    """List files in the project KB."""
    paths = find_paths_or_exit(console)
    if not paths.kb_dir.exists():
        console.print("[yellow]KB directory does not exist yet. Run `devrel kb add` to populate.[/yellow]")
        return
    files = sorted(paths.kb_dir.rglob("*.md"))
    if not files:
        console.print("[dim]No files in KB.[/dim]")
        return
    t = Table(title="Knowledge base files")
    t.add_column("Category"); t.add_column("File"); t.add_column("Size")
    for f in files:
        rel = f.relative_to(paths.kb_dir)
        size = f.stat().st_size
        category = rel.parts[0] if len(rel.parts) > 1 else "—"
        t.add_row(category, str(rel), f"{size:,} bytes")
    console.print(t)


@kb_app.command("refresh")
def refresh() -> None:
    """Re-harvest every configured KB source."""
    paths = find_paths_or_exit(console)
    harvester = KBHarvester(kb_root=paths.kb_dir)

    async def _do() -> None:
        results = await harvester.harvest_all()
        succ = sum(1 for r in results if r)
        console.print(f"[green]✓[/green] {succ}/{len(results)} sources refreshed")

    asyncio.run(_do())
```

If `KBHarvester(kb_root=...)` doesn't accept that kwarg, inspect the existing class and adapt — pass whatever the existing constructor takes (look at `tools/kb_harvester.py`).

- [ ] **Step 2: Create `cli/schedule.py`**

```python
"""`devrel schedule {install, list, remove}` — cron management."""

from __future__ import annotations

import typer
from rich.console import Console

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.tools.scheduler import Scheduler

console = Console()

schedule_app = typer.Typer(
    name="schedule",
    help="Cron schedule management for the weekly pipeline.",
    no_args_is_help=True,
    add_completion=False,
)


@schedule_app.command("install")
def install_cmd() -> None:
    """Install the cron schedule for the weekly pipeline."""
    find_paths_or_exit(console)
    s = Scheduler()
    s.install_cron()
    console.print("[green]✓[/green] Cron schedule installed.")


@schedule_app.command("list")
def list_cmd() -> None:
    """Show installed schedule entries."""
    find_paths_or_exit(console)
    s = Scheduler()
    entries = s.list_entries()
    if not entries:
        console.print("[dim]No entries installed.[/dim]")
        return
    for e in entries:
        console.print(f"  {e}")


@schedule_app.command("remove")
def remove_cmd() -> None:
    """Remove the cron schedule."""
    find_paths_or_exit(console)
    s = Scheduler()
    s.remove_cron()
    console.print("[green]✓[/green] Cron schedule removed.")
```

If `Scheduler()` requires arguments or has different method names, adapt accordingly. Inspect first:
```bash
grep -n "class Scheduler\|def install\|def remove\|def list" src/devrel_swarm/tools/scheduler.py | head -10
```

- [ ] **Step 3: Register both in `cli/__init__.py`**

```python
from devrel_swarm.cli.kb import kb_app
from devrel_swarm.cli.schedule import schedule_app
# ...
app.add_typer(kb_app, name="kb")
app.add_typer(schedule_app, name="schedule")
```

- [ ] **Step 4: Write tests**

Create `tests/cli/test_kb_verbs.py`:

```python
"""Tests for `devrel kb {add, list, refresh}`."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_kb_add_calls_harvester(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.kb.KBHarvester") as MockH:
        inst = MockH.return_value
        inst.harvest_url = AsyncMock(return_value=True)
        r = _run(tmp_path, ["kb", "add", "https://example.com/docs"])
    assert r.exit_code == 0, r.output
    inst.harvest_url.assert_awaited_once()
    assert inst.harvest_url.await_args.args[0] == "https://example.com/docs"


def test_kb_list_empty_kb(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["kb", "list"])
    assert r.exit_code == 0


def test_kb_list_with_files(tmp_path):
    _init(tmp_path)
    kb = tmp_path / ".devrel" / "kb" / "docs"
    kb.mkdir(parents=True)
    (kb / "intro.md").write_text("# Intro\n")
    (kb / "api.md").write_text("# API\n")
    r = _run(tmp_path, ["kb", "list"])
    assert r.exit_code == 0
    assert "intro.md" in r.output
    assert "api.md" in r.output


def test_kb_refresh_calls_harvest_all(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.kb.KBHarvester") as MockH:
        inst = MockH.return_value
        inst.harvest_all = AsyncMock(return_value=[True, True, False])
        r = _run(tmp_path, ["kb", "refresh"])
    assert r.exit_code == 0, r.output
    assert "2/3" in r.output
```

Create `tests/cli/test_schedule_verbs.py`:

```python
"""Tests for `devrel schedule {install, list, remove}`."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_schedule_install(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.schedule.Scheduler") as MockS:
        inst = MockS.return_value
        r = _run(tmp_path, ["schedule", "install"])
    assert r.exit_code == 0, r.output
    inst.install_cron.assert_called_once()


def test_schedule_list_empty(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.schedule.Scheduler") as MockS:
        inst = MockS.return_value
        inst.list_entries.return_value = []
        r = _run(tmp_path, ["schedule", "list"])
    assert r.exit_code == 0
    assert "No entries" in r.output


def test_schedule_remove(tmp_path):
    _init(tmp_path)
    with patch("devrel_swarm.cli.schedule.Scheduler") as MockS:
        inst = MockS.return_value
        r = _run(tmp_path, ["schedule", "remove"])
    assert r.exit_code == 0
    inst.remove_cron.assert_called_once()
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/cli/test_kb_verbs.py tests/cli/test_schedule_verbs.py -v --no-cov 2>&1 | tail -10
```
Expected: 7 passed (4 + 3).

```bash
git add src/devrel_swarm/cli/kb.py src/devrel_swarm/cli/schedule.py \
        src/devrel_swarm/cli/__init__.py \
        tests/cli/test_kb_verbs.py tests/cli/test_schedule_verbs.py
git commit -m "feat(cli): add 'devrel kb' and 'devrel schedule' subcommands"
```

---

## Task 7: Cost, deliverables, config, content slop — utility verbs

**Files:**
- Create: `src/devrel_swarm/cli/cost.py`
- Create: `src/devrel_swarm/cli/deliverables.py`
- Create: `src/devrel_swarm/cli/config.py`
- Modify: `src/devrel_swarm/cli/content.py` (add `slop` subcommand)
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_cost_command.py`, `test_deliverables_command.py`, `test_config_command.py`, `test_content_slop.py`

- [ ] **Step 1: `cli/cost.py`**

```python
"""`devrel cost` — token + USD ledger from .devrel/state.db."""

from __future__ import annotations

import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit

console = Console()


def cost_command(
    month: str = typer.Option("", "--month", help="Filter to a YYYY-MM slice."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show LLM token + cost ledger from .devrel/state.db."""
    paths = find_paths_or_exit(console)
    if not paths.state_db.is_file():
        console.print("[yellow]No state.db yet — run an agent first.[/yellow]")
        raise typer.Exit(code=0)

    where = ""
    params: tuple = ()
    if month:
        where = "WHERE recorded_at LIKE ?"
        params = (f"{month}%",)

    with sqlite3.connect(paths.state_db) as conn:
        rows = list(conn.execute(
            f"SELECT agent, model, SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
            f"FROM costs {where} GROUP BY agent, model ORDER BY SUM(cost_usd) DESC",
            params,
        ))
        total = conn.execute(
            f"SELECT SUM(cost_usd), SUM(input_tokens + output_tokens) FROM costs {where}",
            params,
        ).fetchone()

    if json_output:
        import json as _json
        payload = {
            "rows": [
                {"agent": a, "model": m, "input_tokens": i, "output_tokens": o, "cost_usd": c}
                for a, m, i, o, c in rows
            ],
            "total_cost_usd": (total[0] or 0.0),
            "total_tokens": (total[1] or 0),
            "month_filter": month or None,
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    if not rows:
        console.print("[dim]No cost rows recorded yet.[/dim]")
        return
    t = Table(title=f"Cost report{f' — {month}' if month else ''}")
    t.add_column("Agent"); t.add_column("Model"); t.add_column("In")
    t.add_column("Out"); t.add_column("USD", justify="right")
    for a, m, i, o, c in rows:
        t.add_row(a, m, f"{i:,}", f"{o:,}", f"${c or 0:.4f}")
    console.print(t)
    console.print(f"\n[bold]Total:[/bold] ${total[0] or 0:.4f} ({(total[1] or 0):,} tokens)")
```

- [ ] **Step 2: `cli/deliverables.py`**

```python
"""`devrel deliverables {list, show}` — outputs ledger."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit

console = Console()

deliverables_app = typer.Typer(
    name="deliverables",
    help="List and inspect generated deliverables.",
    no_args_is_help=True,
    add_completion=False,
)


@deliverables_app.command("list")
def list_cmd() -> None:
    """List all deliverables under .devrel/deliverables/."""
    paths = find_paths_or_exit(console)
    if not paths.deliverables_dir.is_dir():
        console.print("[yellow]No deliverables directory yet.[/yellow]")
        return
    files = sorted(
        [p for p in paths.deliverables_dir.iterdir() if p.is_file() and not p.name.endswith("-trace.json")],
        reverse=True,
    )
    if not files:
        console.print("[dim]No deliverables yet.[/dim]")
        return
    t = Table(title="Deliverables")
    t.add_column("File"); t.add_column("Size"); t.add_column("Modified")
    for f in files:
        st = f.stat()
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        t.add_row(f.name, f"{st.st_size:,}", ts)
    console.print(t)


@deliverables_app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Deliverable filename (or substring)."),
) -> None:
    """Print a deliverable's content (rendered if markdown)."""
    paths = find_paths_or_exit(console)
    if not paths.deliverables_dir.is_dir():
        console.print("[red]No deliverables directory.[/red]")
        raise typer.Exit(code=1)
    matches = [p for p in paths.deliverables_dir.iterdir() if name in p.name and p.is_file()]
    if not matches:
        console.print(f"[red]No deliverable matches '{name}'.[/red]")
        raise typer.Exit(code=1)
    f = matches[0]
    body = f.read_text()
    if f.suffix == ".md":
        console.print(Markdown(body))
    else:
        typer.echo(body)
```

- [ ] **Step 3: `cli/config.py`**

```python
"""`devrel config {get, set}` — read/write .devrel/config.toml."""

from __future__ import annotations

import tomli_w
import tomllib

import typer
from rich.console import Console

from devrel_swarm.cli._common import find_paths_or_exit

console = Console()

config_app = typer.Typer(
    name="config",
    help="Read or write .devrel/config.toml.",
    no_args_is_help=True,
    add_completion=False,
)


def _load(paths) -> dict:
    with paths.config_file.open("rb") as f:
        return tomllib.load(f)


def _save(paths, data: dict) -> None:
    with paths.config_file.open("wb") as f:
        tomli_w.dump(data, f)


def _walk(data: dict, key_path: list[str]):
    cur = data
    for k in key_path[:-1]:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    last = key_path[-1]
    if not isinstance(cur, dict) or last not in cur:
        return None
    return cur[last]


@config_app.command("get")
def get_cmd(
    key: str = typer.Argument(..., help="Dotted key path, e.g., 'project.name'."),
) -> None:
    """Read a value from config.toml."""
    paths = find_paths_or_exit(console)
    data = _load(paths)
    val = _walk(data, key.split("."))
    if val is None:
        console.print(f"[yellow]{key}: not set[/yellow]")
        raise typer.Exit(code=1)
    typer.echo(val)


@config_app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="Dotted key path, e.g., 'budget.monthly_usd'."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Write a value to config.toml. Creates intermediate sections as needed."""
    paths = find_paths_or_exit(console)
    data = _load(paths)
    parts = key.split(".")
    cur = data
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    # Best-effort type coercion: int, float, bool, else string.
    coerced: object
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        coerced = int(value)
    else:
        try:
            coerced = float(value)
        except ValueError:
            if value.lower() in ("true", "false"):
                coerced = value.lower() == "true"
            else:
                coerced = value
    cur[parts[-1]] = coerced
    _save(paths, data)
    console.print(f"[green]✓[/green] {key} = {coerced!r}")
```

- [ ] **Step 4: Add `content slop` subcommand**

In `src/devrel_swarm/cli/content.py`, after the existing `audit_command`, add:

```python
@content_app.command("slop")
def slop_command(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Existing draft to slop-check."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run only the anti-slop pass on an existing draft."""
    from devrel_swarm.cli._common import find_paths_or_exit
    from devrel_swarm.quality.slop import find_slop, parse_blocklist

    paths = find_paths_or_exit(console)
    text = file.read_text()
    if not paths.slop_file.is_file():
        console.print("[red]slop-blocklist.md missing.[/red]")
        raise typer.Exit(code=1)
    blocklist = parse_blocklist(paths.slop_file.read_text())
    hits = find_slop(text, blocklist)
    if json_output:
        import json as _json
        typer.echo(_json.dumps(
            {"hits": [{"phrase": h.phrase, "start": h.start, "end": h.end} for h in hits]},
            indent=2,
        ))
        return
    if not hits:
        console.print("[green]✓[/green] No slop hits.")
        return
    console.print(f"[yellow]{len(hits)} slop hit(s):[/yellow]")
    for h in hits:
        console.print(f"  - '{h.phrase}' at offset {h.start}")
```

(Add `from pathlib import Path` import at top if not already present.)

- [ ] **Step 5: Register all in `cli/__init__.py`**

```python
from devrel_swarm.cli.cost import cost_command
from devrel_swarm.cli.deliverables import deliverables_app
from devrel_swarm.cli.config import config_app
# ...
app.command(name="cost")(cost_command)
app.add_typer(deliverables_app, name="deliverables")
app.add_typer(config_app, name="config")
```

- [ ] **Step 6: Write tests**

Create `tests/cli/test_utility_verbs.py` (covers cost, deliverables, config, content slop):

```python
"""Tests for cost, deliverables, config, and content slop verbs."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app
from devrel_swarm.project.state import init_db

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


def _run(tmp_path, args, env_extra=None):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        e = {"ANTHROPIC_API_KEY": "x", **os.environ}
        if env_extra:
            e.update(env_extra)
        return runner.invoke(app, args, env=e)
    finally:
        os.chdir(cwd)


# --- cost ---

def test_cost_empty_db(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["cost"])
    assert r.exit_code == 0


def test_cost_with_rows(tmp_path):
    _init(tmp_path)
    db = tmp_path / ".devrel" / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO costs (agent, model, input_tokens, output_tokens, cost_usd) "
            "VALUES ('kai', 'claude-sonnet-4-5-20250929', 1000, 500, 0.012)"
        )
        conn.commit()
    r = _run(tmp_path, ["cost"])
    assert r.exit_code == 0
    assert "kai" in r.output
    assert "$0.0120" in r.output or "0.012" in r.output


def test_cost_json_output(tmp_path):
    _init(tmp_path)
    db = tmp_path / ".devrel" / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO costs (agent, model, input_tokens, output_tokens, cost_usd) "
            "VALUES ('kai', 'm', 100, 50, 0.001)"
        )
        conn.commit()
    r = _run(tmp_path, ["cost", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert "rows" in data
    assert data["total_cost_usd"] > 0


# --- deliverables ---

def test_deliverables_list_empty(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["deliverables", "list"])
    assert r.exit_code == 0


def test_deliverables_list_with_files(tmp_path):
    _init(tmp_path)
    deliverables = tmp_path / ".devrel" / "deliverables"
    (deliverables / "post.md").write_text("# A post")
    r = _run(tmp_path, ["deliverables", "list"])
    assert r.exit_code == 0
    assert "post.md" in r.output


def test_deliverables_show(tmp_path):
    _init(tmp_path)
    deliverables = tmp_path / ".devrel" / "deliverables"
    (deliverables / "post.md").write_text("# A unique title")
    r = _run(tmp_path, ["deliverables", "show", "post"])
    assert r.exit_code == 0
    assert "unique title" in r.output


def test_deliverables_show_no_match(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["deliverables", "show", "nope"])
    assert r.exit_code != 0


# --- config get/set ---

def test_config_get_existing(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["config", "get", "project.name"])
    assert r.exit_code == 0
    assert "x" in r.output


def test_config_get_missing(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["config", "get", "nonexistent.key"])
    assert r.exit_code != 0


def test_config_set_string(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["config", "set", "project.url", "https://example.com"])
    assert r.exit_code == 0
    r2 = _run(tmp_path, ["config", "get", "project.url"])
    assert "https://example.com" in r2.output


def test_config_set_int(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["config", "set", "budget.monthly_usd", "250"])
    assert r.exit_code == 0
    r2 = _run(tmp_path, ["config", "get", "budget.monthly_usd"])
    assert "250" in r2.output


def test_config_set_bool(tmp_path):
    _init(tmp_path)
    r = _run(tmp_path, ["config", "set", "model.opus_opt_in", "false"])
    assert r.exit_code == 0


# --- content slop ---

def test_content_slop_clean_text(tmp_path):
    _init(tmp_path)
    f = tmp_path / "draft.md"
    f.write_text("Direct, sharp content with no flagged phrases.")
    r = _run(tmp_path, ["content", "slop", str(f)])
    assert r.exit_code == 0
    assert "No slop hits" in r.output


def test_content_slop_dirty_text(tmp_path):
    _init(tmp_path)
    # Add 'delve' to slop-blocklist (it's already there by default).
    f = tmp_path / "draft.md"
    f.write_text("Let us delve into this tapestry.")
    r = _run(tmp_path, ["content", "slop", str(f)])
    assert r.exit_code == 0
    assert "delve" in r.output.lower() or "slop hit" in r.output.lower()


def test_content_slop_json_mode(tmp_path):
    _init(tmp_path)
    f = tmp_path / "draft.md"
    f.write_text("Delve into the tapestry.")
    r = _run(tmp_path, ["content", "slop", str(f), "--json"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert "hits" in data
```

- [ ] **Step 7: Run + commit**

```bash
python -m pytest tests/cli/test_utility_verbs.py -v --no-cov 2>&1 | tail -20
```
Expected: 14 passed.

```bash
git add src/devrel_swarm/cli/cost.py src/devrel_swarm/cli/deliverables.py \
        src/devrel_swarm/cli/config.py src/devrel_swarm/cli/content.py \
        src/devrel_swarm/cli/__init__.py tests/cli/test_utility_verbs.py
git commit -m "feat(cli): add cost, deliverables, config get/set, content slop"
```

---

## Task 8: Niche verbs — docs build, video record

Two more thin wrappers around agents.

**Files:**
- Create: `src/devrel_swarm/cli/docs.py`, `src/devrel_swarm/cli/video.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Create: `tests/cli/test_niche_verbs.py`

**Specs:**

| Verb | Agent | Task |
|------|-------|------|
| `docs build` | `dex` | `"Build architecture docs and API reference"` |
| `video record` | `vox` | `f"Record video tutorial: {script}"` |

- [ ] **Step 1: `cli/docs.py`**

```python
"""`devrel docs build` — AST-based docs via Dex."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

docs_app = typer.Typer(
    name="docs",
    help="Documentation generation.",
    no_args_is_help=True,
    add_completion=False,
)


@docs_app.command("build")
def build(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build architecture docs + API reference from source via Dex."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("dex", "Build architecture docs and API reference")
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 2: `cli/video.py`**

```python
"""`devrel video record` — screen-recorded tutorials via Vox."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

video_app = typer.Typer(
    name="video",
    help="Video tutorial production.",
    no_args_is_help=True,
    add_completion=False,
)


@video_app.command("record")
def record(
    script: str = typer.Argument(..., help="Path to script markdown OR raw task description."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Record a screen-recorded video tutorial via Vox."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("vox", f"Record video tutorial: {script}")
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
```

- [ ] **Step 3: Register both**

```python
from devrel_swarm.cli.docs import docs_app
from devrel_swarm.cli.video import video_app
# ...
app.add_typer(docs_app, name="docs")
app.add_typer(video_app, name="video")
```

- [ ] **Step 4: Tests**

Create `tests/cli/test_niche_verbs.py`:

```python
"""Tests for `devrel docs build` and `devrel video record`."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from devrel_swarm.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(app, ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""])
    finally:
        os.chdir(cwd)


@pytest.fixture
def mock_atlas():
    with patch("devrel_swarm.cli._common.Atlas") as M:
        inst = M.return_value
        inst.run_single_task = AsyncMock(return_value=MagicMock(success=True, agent="?", result="ok", error=None))
        yield inst


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args, env={"ANTHROPIC_API_KEY": "x", **os.environ})
    finally:
        os.chdir(cwd)


def test_docs_build_dispatches_to_dex(tmp_path, mock_atlas):
    _init(tmp_path)
    r = _run(tmp_path, ["docs", "build"])
    assert r.exit_code == 0, r.output
    agent, _ = mock_atlas.run_single_task.await_args.args
    assert agent == "dex"


def test_video_record_dispatches_to_vox(tmp_path, mock_atlas):
    _init(tmp_path)
    r = _run(tmp_path, ["video", "record", "Tutorial on widgets"])
    assert r.exit_code == 0, r.output
    agent, task = mock_atlas.run_single_task.await_args.args
    assert agent == "vox"
    assert "widgets" in task
```

- [ ] **Step 5: Run + commit**

```bash
python -m pytest tests/cli/test_niche_verbs.py -v --no-cov 2>&1 | tail -10
```
Expected: 2 passed.

```bash
git add src/devrel_swarm/cli/docs.py src/devrel_swarm/cli/video.py \
        src/devrel_swarm/cli/__init__.py tests/cli/test_niche_verbs.py
git commit -m "feat(cli): add 'devrel docs build' and 'devrel video record'"
```

---

## Task 9: Verify, document, finalize

- [ ] **Step 1: Full test suite + parity**

```bash
python -m pytest tests/ -q --no-header --tb=short --no-cov 2>&1 | tee /tmp/pytest.phase4.after.txt | tail -10
diff /tmp/pytest.failures.phase4.before.txt <(grep "^FAILED" /tmp/pytest.phase4.after.txt | sort)
```
Expected: ~`710-720 passed, 22 failed`. Diff empty.

- [ ] **Step 2: Coverage check**

```bash
python -m pytest tests/cli tests/core tests/project \
  --cov=devrel_swarm.cli --cov=devrel_swarm.core.llm --cov=devrel_swarm.project \
  --cov-report=term 2>&1 | tail -20
```
Expected: `cli/` ≥80%, `project/cost_sink.py` ≥80%, `core/llm.py` ≥75% (the cost-sink portions).

- [ ] **Step 3: End-to-end smoke test**

```bash
T=$(mktemp -d) && cd "$T"
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name probe --url https://probe.dev --github-repo probe/probe >/dev/null
devrel --help | head -30
devrel kb list
devrel deliverables list
devrel cost
devrel config get project.name
devrel config set budget.monthly_usd 200
devrel config get budget.monthly_usd
cd - && rm -rf "$T"
```

All must exit 0 (except `cost` may print "no rows" — still exit 0).

- [ ] **Step 4: Update CLAUDE.md**

Add the new command list to `## Commands`:

```bash
# Pipelines
devrel run                                    # full weekly cycle
devrel run --health                           # health check only
devrel run --agent kai --task "Write tutorial"

# DevRel
devrel triage --days 7
devrel listen --platforms reddit,hn
devrel synthesize
devrel experiment "Hypothesis text"

# Sales / Marketing
devrel intel <competitor>
devrel sales {outreach|battlecard|sequence} <arg>
devrel marketing {blog|landing|social|campaign} <arg>

# KB / Schedule
devrel kb {add|list|refresh}
devrel schedule {install|list|remove}

# Utilities
devrel cost [--month YYYY-MM]
devrel deliverables {list|show <name>}
devrel config {get|set} <key> [value]
devrel content slop <file>

# Niche
devrel docs build
devrel video record <script>
```

In the File Map, append after the existing CLI block:

```
src/devrel_swarm/cli/_common.py    Shared CLI helpers (find_paths_or_exit,
                                   build_atlas_or_exit, render_result).
src/devrel_swarm/cli/run.py + 14 more  One file per verb / verb group.
src/devrel_swarm/project/cost_sink.py  Builds an async sink that writes
                                   LLM cost events into .devrel/state.db.
                                   Atlas registers it on construction
                                   when project_paths is provided.
```

- [ ] **Step 5: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Phase 4 CLI command reference and File Map entries"
```

- [ ] **Step 6: Final verification**

```bash
git log --oneline main..HEAD
devrel --version
devrel --help
```

Expected: ~9 commits on the branch, version `0.2.0`, the help shows every Phase 4 command.

---

## Self-review checklist (already applied)

- **Spec coverage:** every command listed in the spec's §3 CLI surface (except `devrel ask`, which the spec defers to v1.1) is wired up: init/doctor/cost/run/triage/listen/synthesize/experiment/content/docs/video/intel/sales/marketing/kb/config/schedule/deliverables. The `devrel ask` natural-language router stays deferred.
- **No placeholders:** every step has explicit code or commands. The "Common pattern" section centralizes boilerplate so per-verb tasks stay short.
- **Type / name consistency:** the helpers `find_paths_or_exit`, `build_atlas_or_exit`, `render_result`, `find_devrel_root`, `ProjectPaths`, `Atlas.run_single_task`, `Atlas.run_weekly_cycle`, `make_sqlite_sink`, `set_cost_sink`, `_emit_cost` all use consistent names across tasks.
- **Cost trade-off:** Phase 4 wires the cost ledger so `devrel cost` is real, not a stub. The `BudgetGate` enforcement piece from the v0 branch is **deferred to a hypothetical Phase 5** — Phase 4 records costs but doesn't yet enforce caps.

## Out of scope (deferred)

- `devrel ask` natural-language router (spec defers to v1.1).
- `BudgetGate` cap enforcement (records costs in Phase 4; enforces in Phase 5 if traction warrants).
- `devrel run --devrel | --sales | --marketing` flag variants (the agents-of-a-cycle subset). Phase 4 ships full weekly + `--health` + `--agent` only. Sub-cycle variants would need new Atlas methods and aren't load-bearing for an MVP CLI.
- Archiving `product/v0-agentic-alpha` branch — Phase 5.
- README rewrite — Phase 5.
