"""Tests for shared LLM client wrapper."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devrel_origin.core.llm import CritiqueResult, LLMClient


class TestLLMClient:
    """Test LLMClient.generate() wrapper."""

    @pytest.mark.asyncio
    async def test_generate_returns_text(self):
        client = LLMClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Generated content here")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch.object(
            client._client.messages, "create", new_callable=AsyncMock, return_value=mock_response
        ):
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

        with patch.object(
            client._client.messages, "create", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.generate(
                system_prompt="Extract JSON.",
                user_prompt="Analyze this.",
            )
        assert '"themes"' in result

    def test_default_model(self):
        client = LLMClient(api_key="test-key")
        assert client.model == "claude-sonnet-4-5-20250929"


class TestCritiqueResult:
    """Test CritiqueResult parsing."""

    def test_from_valid_json(self):
        raw = json.dumps(
            {
                "overall_score": 8,
                "issues": [
                    {"criterion": "clarity", "severity": "low", "description": "ok", "fix": "n/a"}
                ],
                "strengths": ["well-structured"],
            }
        )
        result = CritiqueResult.from_json(raw)
        assert result.overall_score == 8
        assert result.revision_needed is False  # score >= 7, no high-severity
        assert len(result.issues) == 1
        assert result.strengths == ["well-structured"]

    def test_revision_needed_computed_from_score(self):
        result = CritiqueResult(overall_score=5, issues=[])
        assert result.revision_needed is True  # score < 7

    def test_revision_needed_computed_from_high_severity(self):
        result = CritiqueResult(
            overall_score=8,
            issues=[{"severity": "high", "description": "critical flaw"}],
        )
        assert result.revision_needed is True  # high-severity issue

    def test_from_json_with_markdown_fences(self):
        raw = '```json\n{"overall_score": 6, "issues": [], "strengths": []}\n```'
        result = CritiqueResult.from_json(raw)
        assert result.overall_score == 6
        assert result.revision_needed is True  # score < 7

    def test_from_invalid_json_returns_default(self):
        # Default flipped from False to True: when the critique JSON is
        # unparseable, the safe choice is to err toward another revision pass
        # rather than ship something the editor never actually evaluated.
        result = CritiqueResult.from_json("not json at all")
        assert result.overall_score == 5
        assert result.revision_needed is True


class TestGenerateWithRevision:
    """Test the critique-revise loop."""

    def _mock_response(self, text):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 50
        return resp

    @pytest.mark.asyncio
    async def test_skips_revision_when_score_high(self):
        """Score >= 7 and revision_needed=False → no revision round."""
        client = LLMClient(api_key="test-key")
        critique_json = json.dumps(
            {
                "overall_score": 8,
                "issues": [],
                "strengths": ["great"],
                "revision_needed": False,
            }
        )
        client._client.messages.create = AsyncMock(
            side_effect=[
                self._mock_response("Draft content"),
                self._mock_response(critique_json),
            ]
        )

        content, trace = await client.generate_with_revision(
            system_prompt="sys",
            user_prompt="task",
        )
        assert content == "Draft content"
        assert trace.revision_rounds == 0
        assert trace.final_score == 8

    @pytest.mark.asyncio
    async def test_revises_when_score_low(self):
        """Score < 7 → triggers revision, then re-critique."""
        client = LLMClient(api_key="test-key")
        low_critique = json.dumps(
            {
                "overall_score": 4,
                "issues": [
                    {
                        "criterion": "accuracy",
                        "severity": "high",
                        "description": "wrong",
                        "fix": "fix it",
                    }
                ],
                "strengths": [],
            }
        )
        high_critique = json.dumps(
            {
                "overall_score": 8,
                "issues": [],
                "strengths": ["fixed"],
            }
        )
        client._client.messages.create = AsyncMock(
            side_effect=[
                self._mock_response("Bad draft"),  # initial generate
                self._mock_response(low_critique),  # critique → low score
                self._mock_response("Revised draft"),  # revision
                self._mock_response(high_critique),  # critique → high score
            ]
        )

        content, trace = await client.generate_with_revision(
            system_prompt="sys",
            user_prompt="task",
        )
        assert content == "Revised draft"
        assert trace.revision_rounds == 1
        assert trace.final_score == 8
        assert len(trace.drafts) == 2

    @pytest.mark.asyncio
    async def test_stops_at_max_rounds(self):
        """Revision loop stops after max_rounds even if score stays low."""
        client = LLMClient(api_key="test-key")
        low_critique = json.dumps(
            {
                "overall_score": 3,
                "issues": [
                    {"criterion": "x", "severity": "high", "description": "bad", "fix": "redo"}
                ],
                "strengths": [],
            }
        )
        client._client.messages.create = AsyncMock(
            side_effect=[
                self._mock_response("Draft v1"),  # initial generate
                self._mock_response(low_critique),  # critique 1 → low, revise
                self._mock_response("Draft v2"),  # revision 1
                self._mock_response(low_critique),  # critique 2 → low, revise
                self._mock_response("Draft v3"),  # revision 2
            ]
        )

        content, trace = await client.generate_with_revision(
            system_prompt="sys",
            user_prompt="task",
            max_rounds=2,
        )
        assert content == "Draft v3"
        assert trace.revision_rounds == 2
        assert len(trace.drafts) == 3
