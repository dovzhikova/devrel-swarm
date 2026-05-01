"""Tests for Sentinel brand-audit agent."""

from pathlib import Path
from unittest.mock import MagicMock

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
