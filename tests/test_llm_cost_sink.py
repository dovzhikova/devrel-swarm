"""LLMClient cost_sink hook tests — exercised without real Anthropic calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_generate_triggers_cost_sink():
    """generate() must invoke the sink exactly once per API call with the right args."""
    captured: list[dict] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        captured.append({"agent": agent, "model": model, "usage": usage})

    client = LLMClient()
    client.set_agent("kai")
    client.set_cost_sink(sink)

    # Build a fake Anthropic response shape
    fake_text = MagicMock()
    fake_text.text = "hello"
    fake_response = MagicMock()
    fake_response.content = [fake_text]
    fake_response.usage.input_tokens = 200
    fake_response.usage.output_tokens = 75
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.cache_read_input_tokens = 0

    client._client.messages.create = AsyncMock(return_value=fake_response)

    result = await client.generate(
        system_prompt="sys", user_prompt="usr", temperature=0.0,
    )

    assert result == "hello"
    assert len(captured) == 1
    assert captured[0]["agent"] == "kai"
    # resolved_model is the default (sonnet 4.5 per DEFAULT_MODEL) since we
    # didn't override; we care about the usage payload shape, not the exact model.
    assert captured[0]["usage"]["input_tokens"] == 200
    assert captured[0]["usage"]["output_tokens"] == 75


@pytest.mark.asyncio
async def test_sink_exception_does_not_break_generate():
    """A misbehaving sink must not fail generate() — generation succeeds, sink error is logged."""
    async def bad_sink(agent: str, model: str, usage: dict) -> None:
        raise RuntimeError("sink broke")

    client = LLMClient()
    client.set_agent("kai")
    client.set_cost_sink(bad_sink)

    fake_text = MagicMock()
    fake_text.text = "hi"
    fake_response = MagicMock()
    fake_response.content = [fake_text]
    fake_response.usage.input_tokens = 10
    fake_response.usage.output_tokens = 5
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.cache_read_input_tokens = 0

    client._client.messages.create = AsyncMock(return_value=fake_response)

    # Should return normally despite the sink raising
    result = await client.generate(system_prompt="s", user_prompt="u")
    assert result == "hi"
