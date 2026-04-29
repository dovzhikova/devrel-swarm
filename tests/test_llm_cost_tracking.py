"""Tests for LLM token usage tracking."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.llm import LLMClient, TokenUsage


class TestTokenUsage:
    """Test TokenUsage dataclass."""

    def test_initial_state(self):
        usage = TokenUsage()
        assert usage.total_input_tokens == 0
        assert usage.total_output_tokens == 0
        assert usage.total_calls == 0

    def test_record_single_call(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        assert usage.total_input_tokens == 100
        assert usage.total_output_tokens == 50
        assert usage.total_calls == 1

    def test_record_multiple_calls(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        usage.record(input_tokens=200, output_tokens=75)
        usage.record(input_tokens=50, output_tokens=25)
        assert usage.total_input_tokens == 350
        assert usage.total_output_tokens == 150
        assert usage.total_calls == 3

    def test_to_dict(self):
        usage = TokenUsage()
        usage.record(input_tokens=100, output_tokens=50)
        d = usage.to_dict()
        assert d == {
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_calls": 1,
        }

    def test_to_dict_empty(self):
        usage = TokenUsage()
        d = usage.to_dict()
        assert d == {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_calls": 0,
        }


class TestLLMClientUsageTracking:
    """Test that LLMClient tracks usage across generate() calls."""

    def test_client_initializes_with_empty_usage(self):
        client = LLMClient(api_key="test-key")
        assert client.usage.total_calls == 0
        assert client.usage.total_input_tokens == 0

    @pytest.mark.asyncio
    async def test_generate_tracks_usage(self):
        client = LLMClient(api_key="test-key")

        # Mock the Anthropic response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello world")]
        mock_response.usage.input_tokens = 150
        mock_response.usage.output_tokens = 42

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.generate(
            system_prompt="You are helpful.",
            user_prompt="Say hello.",
        )

        assert client.usage.total_calls == 1
        assert client.usage.total_input_tokens == 150
        assert client.usage.total_output_tokens == 42

    @pytest.mark.asyncio
    async def test_generate_accumulates_usage(self):
        client = LLMClient(api_key="test-key")

        mock_response_1 = MagicMock()
        mock_response_1.content = [MagicMock(text="Response 1")]
        mock_response_1.usage.input_tokens = 100
        mock_response_1.usage.output_tokens = 30

        mock_response_2 = MagicMock()
        mock_response_2.content = [MagicMock(text="Response 2")]
        mock_response_2.usage.input_tokens = 200
        mock_response_2.usage.output_tokens = 60

        client._client.messages.create = AsyncMock(
            side_effect=[mock_response_1, mock_response_2]
        )

        await client.generate(system_prompt="s", user_prompt="p1")
        await client.generate(system_prompt="s", user_prompt="p2")

        assert client.usage.total_calls == 2
        assert client.usage.total_input_tokens == 300
        assert client.usage.total_output_tokens == 90
