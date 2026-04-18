"""SQLite storage layer for deliverables, signals, cost events, checkpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tools.storage import InstanceStorage


@pytest.fixture
async def storage():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        s = InstanceStorage(db_path=str(db_path))
        await s.init()
        yield s
        await s.close()


@pytest.mark.asyncio
async def test_create_job_and_record_cost(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.record_cost(
        job_id=job_id, agent="kai", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        cost_cents=1.05,
    )
    total = await storage.monthly_spend_cents()
    assert abs(total - 1.05) < 0.01


@pytest.mark.asyncio
async def test_insert_deliverable(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    deliverable_id = await storage.insert_deliverable(
        job_id=job_id, kind="tutorial", title="Hello", body_md="# hi",
        quality_score=8.2,
    )
    rows = await storage.list_deliverables(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == deliverable_id
    assert rows[0]["title"] == "Hello"


@pytest.mark.asyncio
async def test_update_job_status(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.update_job(job_id, status="completed")
    rows = await storage.list_jobs(limit=5)
    assert rows[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_save_and_latest_checkpoint(storage):
    job_id = await storage.create_job(kind="weekly_cycle")
    await storage.save_checkpoint(job_id, stage="s1", payload={"foo": "bar"})
    await storage.save_checkpoint(job_id, stage="s2", payload={"foo": "baz"})
    latest = await storage.latest_checkpoint(job_id)
    assert latest["stage"] == "s2"
    assert latest["payload"]["foo"] == "baz"
