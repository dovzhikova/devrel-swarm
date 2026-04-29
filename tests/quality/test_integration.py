"""Tests for generate_with_pipeline helper used by content agents."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devrel_swarm.quality import generate_with_pipeline


@pytest.mark.asyncio
async def test_falls_back_when_no_devrel_project(tmp_path, monkeypatch):
    """When find_devrel_root raises, the helper uses generate_with_revision."""
    monkeypatch.chdir(tmp_path)  # tmp_path has no .devrel/
    client = MagicMock()
    client.generate = AsyncMock(return_value=("initial draft", None))
    legacy_trace = MagicMock(critiques=[MagicMock(strengths=["s1"], issues=["i1"])])
    client.generate_with_revision = AsyncMock(return_value=("legacy text", legacy_trace))

    text, strengths, issues = await generate_with_pipeline(
        llm_client=client,
        system_prompt="sys",
        user_prompt="usr",
        content_type="tutorial",
        logger=logging.getLogger("test"),
    )
    assert text == "legacy text"
    assert strengths == ["s1"]
    assert issues == ["i1"]
    client.generate_with_revision.assert_awaited_once()


@pytest.mark.asyncio
async def test_uses_pipeline_when_devrel_project_present(tmp_path, monkeypatch):
    """When .devrel/ exists, the helper calls run_pipeline."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / "config.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.generate = AsyncMock(return_value=("initial draft", None))
    fake_result = MagicMock(
        final_text="pipeline output",
        stages=[MagicMock(detail="stage detail", issues=["pipeline issue"])],
    )
    with patch("devrel_swarm.quality.editorial.run_pipeline", new=AsyncMock(return_value=fake_result)):
        text, strengths, issues = await generate_with_pipeline(
            llm_client=client,
            system_prompt="sys",
            user_prompt="usr",
            content_type="tutorial",
            logger=logging.getLogger("test"),
        )
    assert text == "pipeline output"
    assert strengths == ["stage detail"]
    assert issues == ["pipeline issue"]


@pytest.mark.asyncio
async def test_falls_back_on_abort_loud(tmp_path, monkeypatch):
    """When run_pipeline raises AbortLoud, helper falls back to legacy."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / "config.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.generate = AsyncMock(return_value=("initial draft", None))
    legacy_trace = MagicMock(critiques=[MagicMock(strengths=[], issues=[])])
    client.generate_with_revision = AsyncMock(return_value=("legacy fallback", legacy_trace))
    from devrel_swarm.quality.editorial import AbortLoud
    with patch(
        "devrel_swarm.quality.editorial.run_pipeline",
        new=AsyncMock(side_effect=AbortLoud("slop persisted: delve")),
    ):
        text, strengths, issues = await generate_with_pipeline(
            llm_client=client,
            system_prompt="sys",
            user_prompt="usr",
            content_type="tutorial",
            logger=logging.getLogger("test"),
        )
    assert text == "legacy fallback"
    client.generate_with_revision.assert_awaited_once()
