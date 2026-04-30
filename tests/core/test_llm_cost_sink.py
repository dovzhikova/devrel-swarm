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
