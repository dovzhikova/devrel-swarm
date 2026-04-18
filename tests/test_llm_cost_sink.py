"""LLMClient cost_sink hook tests — exercised without real Anthropic calls."""

from __future__ import annotations

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
    assert captured[0]["usage"]["output_tokens"] == 50
    assert captured[0]["usage"]["cache_creation_input_tokens"] == 10
    assert captured[0]["usage"]["cache_read_input_tokens"] == 20


@pytest.mark.asyncio
async def test_emit_cost_noop_without_sink():
    """Without a sink registered, _emit_cost must be a safe no-op."""
    client = LLMClient()
    # should not raise
    await client._emit_cost(
        model="claude-sonnet-4-6",
        input_tokens=1, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )


@pytest.mark.asyncio
async def test_emit_cost_uses_unknown_agent_when_not_set():
    captured: list[dict] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        captured.append({"agent": agent, "model": model, "usage": usage})

    client = LLMClient()
    # do NOT call set_agent
    client.set_cost_sink(sink)
    await client._emit_cost(
        model="claude-sonnet-4-6",
        input_tokens=1, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    assert captured[0]["agent"] == "unknown"
