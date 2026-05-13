"""Tests for LLMClient cost-sink behavior under cancellation.

The cost sink writes cost rows to .devrel/state.db. Atlas's per-agent timeout
fires `asyncio.CancelledError` at the wrapped agent's task; the prior shape
of `_emit_cost` caught only `Exception`, so cancellation between an LLM
response returning and the cost row being persisted dropped the row silently.
The shield around the sink call now keeps the SQLite write running even when
the outer task is being cancelled.
"""

from __future__ import annotations

import asyncio

import pytest

from devrel_origin.core.llm import LLMClient


@pytest.mark.asyncio
async def test_emit_cost_runs_under_outer_cancellation():
    """When the outer task is cancelled mid-emit, the sink must still record."""
    received: list[tuple[str, str, dict]] = []
    sink_started = asyncio.Event()
    sink_complete = asyncio.Event()

    async def sink(agent: str, model: str, usage: dict) -> None:
        sink_started.set()
        # One yield so cancellation has a chance to hit before we record.
        await asyncio.sleep(0)
        received.append((agent, model, usage))
        sink_complete.set()

    client = LLMClient(api_key="x")
    client.set_cost_sink(sink)

    async def emit_with_marker():
        await client._emit_cost("claude-sonnet-4-5-20250929", 100, 50)

    task = asyncio.create_task(emit_with_marker())
    # Yield until the sink is in flight, then cancel.
    await sink_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The shielded sink must complete despite the outer cancellation.
    await asyncio.wait_for(sink_complete.wait(), timeout=1.0)
    assert len(received) == 1
    agent, model, usage = received[0]
    assert model == "claude-sonnet-4-5-20250929"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50


@pytest.mark.asyncio
async def test_emit_cost_no_sink_returns_silently():
    """No-sink path is a no-op."""
    client = LLMClient(api_key="x")
    # No set_cost_sink call; default is None.
    await client._emit_cost("claude-sonnet-4-5-20250929", 100, 50)
    # No assertion needed; absence of error is the test.


@pytest.mark.asyncio
async def test_emit_cost_swallows_sink_exceptions():
    """Sink errors must not break the LLM call path."""

    async def broken_sink(agent: str, model: str, usage: dict) -> None:
        raise RuntimeError("DB locked")

    client = LLMClient(api_key="x")
    client.set_cost_sink(broken_sink)

    # Should not raise.
    await client._emit_cost("claude-sonnet-4-5-20250929", 100, 50)


@pytest.mark.asyncio
async def test_emit_cost_uses_current_agent_var():
    """ContextVar attribution wins over the instance attribute."""
    received: list[str] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        received.append(agent)

    client = LLMClient(api_key="x")
    client.set_cost_sink(sink)

    with client.agent_context("kai"):
        await client._emit_cost("claude-sonnet-4-5-20250929", 100, 50)

    assert received == ["kai"]
