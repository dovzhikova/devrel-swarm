"""Tests for slop blocklist matching, LLM lint, and force-rewrite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.quality.slop import (
    SlopHit,
    find_slop,
    force_rewrite,
    llm_lint,
    parse_blocklist,
)


def test_parse_blocklist_strips_comments_and_blanks():
    md = """# Anti-slop blocklist

## Hedge words
delve
furthermore

## CTAs
learn more
get started today
"""
    out = parse_blocklist(md)
    assert out == ["delve", "furthermore", "learn more", "get started today"]


def test_parse_blocklist_lowercases():
    out = parse_blocklist("Delve\nFURTHERMORE\n")
    assert out == ["delve", "furthermore"]


def test_find_slop_word_boundary_match():
    text = "We delve into the topic, furthermore the tapestry unfolds."
    hits = find_slop(text, ["delve", "furthermore", "tapestry"])
    assert {h.phrase for h in hits} == {"delve", "furthermore", "tapestry"}


def test_find_slop_case_insensitive():
    text = "DELVE into this. Furthermore."
    hits = find_slop(text, ["delve", "furthermore"])
    assert len(hits) == 2


def test_find_slop_does_not_match_substrings():
    """`delve` should not match `delivery` or `develop`."""
    text = "We develop and delivery great things."
    hits = find_slop(text, ["delve"])
    assert hits == []


def test_find_slop_handles_multi_word_phrases():
    text = "Get started today with our platform."
    hits = find_slop(text, ["get started today"])
    assert len(hits) == 1
    assert hits[0].phrase == "get started today"


def test_find_slop_empty_when_no_matches():
    assert find_slop("Direct, sharp, no fluff.", ["delve", "tapestry"]) == []


@pytest.mark.asyncio
async def test_llm_lint_calls_haiku_and_parses_phrases():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("phrase one\nphrase two\n", None))
    out = await llm_lint("some draft text", "voice prose", client)
    assert out == ["phrase one", "phrase two"]
    # Verify it called with model="haiku" for cost.
    call_kwargs = client.generate.await_args.kwargs
    assert call_kwargs.get("model") == "haiku"


@pytest.mark.asyncio
async def test_llm_lint_returns_empty_on_empty_response():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("", None))
    assert await llm_lint("draft", "voice", client) == []


@pytest.mark.asyncio
async def test_llm_lint_filters_blank_lines_and_bullets():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("- phrase one\n  \n* phrase two\n#commented\n", None))
    out = await llm_lint("draft", "voice", client)
    assert out == ["phrase one", "phrase two"]


@pytest.mark.asyncio
async def test_force_rewrite_passes_hits_to_llm_and_returns_text():
    client = MagicMock()
    client.generate = AsyncMock(return_value=("the rewritten text", None))
    hits = [SlopHit(phrase="delve", start=0, end=5)]
    out = await force_rewrite("delve into x", hits, ["extra-slop"], "voice", client)
    assert out == "the rewritten text"
    user_prompt = client.generate.await_args.kwargs["user_prompt"]
    # Must list every flagged item in the rewrite prompt.
    assert "delve" in user_prompt
    assert "extra-slop" in user_prompt
