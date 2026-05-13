"""Tests for the skeptical-dev persona reader test."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_origin.quality.persona import (
    PersonaResult,
    test_against_persona,
)


@pytest.mark.asyncio
async def test_returns_score_and_weak_sections_from_haiku():
    client = MagicMock()
    client.generate = AsyncMock(
        return_value='{"score": 7, "weak_sections": ["The intro hedges too much."], '
        '"feedback": "Solid, but the conclusion is weak."}'
    )
    out = await test_against_persona(
        text="some draft", content_type="tutorial", voice="direct", llm_client=client
    )
    assert isinstance(out, PersonaResult)
    assert out.score == 7
    assert out.weak_sections == ["The intro hedges too much."]
    assert "weak" in out.feedback.lower()


@pytest.mark.asyncio
async def test_uses_haiku_model():
    client = MagicMock()
    client.generate = AsyncMock(return_value='{"score": 8, "weak_sections": [], "feedback": "ok"}')
    await test_against_persona(text="x", content_type="blog_post", voice="", llm_client=client)
    assert client.generate.await_args.kwargs["model"] == "haiku"


@pytest.mark.asyncio
async def test_clamps_score_to_1_10():
    client = MagicMock()
    client.generate = AsyncMock(return_value='{"score": 99, "weak_sections": [], "feedback": "x"}')
    out = await test_against_persona(text="x", content_type="tutorial", voice="", llm_client=client)
    assert 1 <= out.score <= 10


@pytest.mark.asyncio
async def test_falls_back_when_response_not_json():
    client = MagicMock()
    client.generate = AsyncMock(return_value="not json")
    out = await test_against_persona(text="x", content_type="tutorial", voice="", llm_client=client)
    assert out.score == 5  # neutral fallback
    assert "could not parse" in out.feedback.lower()


@pytest.mark.asyncio
async def test_includes_content_type_in_prompt():
    client = MagicMock()
    client.generate = AsyncMock(return_value='{"score": 7, "weak_sections": [], "feedback": "ok"}')
    await test_against_persona(
        text="draft", content_type="cold_email", voice="brief", llm_client=client
    )
    user = client.generate.await_args.kwargs["user_prompt"]
    assert "cold_email" in user or "cold email" in user.lower()
