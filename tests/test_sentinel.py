"""Tests for Sentinel brand-audit agent."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.atlas import SharedContext
from devrel_swarm.core.sentinel import Sentinel


def _make_sentinel() -> Sentinel:
    return Sentinel(
        api_client=MagicMock(),
        knowledge_base_path=Path("/tmp"),
        llm_client=None,
    )


def test_collect_content_pulls_from_each_agents_primary_field():
    """Sentinel must read from each agent's actual primary key, not a universal 'content'.

    Mox stores prose under 'blog_post', Pax under 'body', Rex under 'analysis'.
    A naive implementation that only reads 'content' would silently audit
    one or two agents per week instead of nine.
    """
    ctx = SharedContext(week_of="2026-W18")
    ctx.kai_content = {"content": "Kai prose about feature flags."}
    ctx.mox_campaigns = {"blog_post": "Mox blog prose with full body."}
    ctx.pax_sales = {"body": "Pax email body for outreach."}
    ctx.rex_competitive = {"analysis": "Rex competitive analysis text."}

    sentinel = _make_sentinel()
    pieces = sentinel._collect_content(ctx)
    agents = sorted(p["agent"] for p in pieces)
    assert "kai_content" in agents
    assert "mox_campaigns" in agents
    assert "pax_sales" in agents
    assert "rex_competitive" in agents


def test_collect_content_skips_empty_fields():
    """Empty strings and missing keys must not produce phantom audit pieces."""
    ctx = SharedContext(week_of="2026-W18")
    ctx.kai_content = {"content": ""}
    ctx.mox_campaigns = {}
    sentinel = _make_sentinel()
    pieces = sentinel._collect_content(ctx)
    assert pieces == []


def test_collect_content_handles_list_field():
    """List-typed fields (like sequence) should be joined and audited."""
    ctx = SharedContext(week_of="2026-W18")
    ctx.pax_sales = {"sequence": ["Step 1: hello", "Step 2: follow-up"]}
    sentinel = _make_sentinel()
    pieces = sentinel._collect_content(ctx)
    assert len(pieces) == 1
    assert "Step 1" in pieces[0]["content"]


def test_structural_audit_score_is_in_1_100_range():
    """Structural fallback must produce scores comparable to the LLM 1-100 scale.

    Pre-Phase-7 the structural path multiplied a 1-7 item score by 10 and
    capped at 100, producing a max possible overall score of 70 — wildly
    different from the LLM path. After the fix, clean content should
    score >= 70 on the 1-100 scale.
    """
    sentinel = _make_sentinel()
    pieces = [
        {
            "agent": "kai_content",
            "content_type": "content",
            "content": (
                "# Title\n\nDirect, sharp prose with a heading and short "
                "paragraphs.\n\n## Section\n\nNo buzzwords here."
            ),
        }
    ]
    report = sentinel._structural_audit("audit", pieces)
    assert 0 <= report["overall_score"] <= 100
    # Clean content (heading + short paragraphs + no buzzwords) starts at
    # item score 7, which maps to overall 100 in the new linear scale.
    assert report["overall_score"] >= 70


@pytest.mark.asyncio
async def test_json_parse_failure_logs_distinctly_from_api_error(caplog):
    """JSON parse error and API error should log as different events.

    Operators reading watchdog/run logs need to tell apart "model produced
    invalid JSON" (prompt issue) from "API returned 500" (vendor issue).
    """
    sentinel = _make_sentinel()
    sentinel.llm_client = MagicMock()
    sentinel.llm_client.generate = AsyncMock(return_value="not json at all")

    with caplog.at_level("WARNING"):
        result = await sentinel._llm_audit(
            "audit",
            [{"agent": "kai_content", "content_type": "content", "content": "x"}],
        )

    # Falls back to structural
    assert result["status"] == "audited_structural"
    # Distinct log marker for JSON parse failure
    assert any("non-JSON" in rec.message for rec in caplog.records)
