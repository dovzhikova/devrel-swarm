from unittest.mock import AsyncMock

import pytest

from workers.budget import BudgetGate, CostRecord


def test_cost_record_sonnet_pricing():
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    # $3 * 0.001 + $15 * 0.0005 = $0.0105 = 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


def test_cost_record_cache_pricing():
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=0, output_tokens=100,
        cache_creation_input_tokens=2000,
        cache_read_input_tokens=5000,
    )
    # 2000*$3.75/1M + 5000*$0.30/1M + 100*$15/1M = 0.0075+0.0015+0.0015 = 0.0105 = 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


@pytest.mark.asyncio
async def test_gate_tracks_without_blocking():
    storage = AsyncMock()
    storage.monthly_spend_cents = AsyncMock(return_value=0.0)
    storage.record_cost = AsyncMock()
    gate = BudgetGate(storage=storage, job_id="j1", block_on_exceed=False)
    allowed = await gate.check_and_record(
        CostRecord(model="claude-sonnet-4-6",
                   input_tokens=1000, output_tokens=500,
                   cache_creation_input_tokens=0, cache_read_input_tokens=0),
        agent="kai",
    )
    assert allowed is True
    storage.record_cost.assert_called_once()


@pytest.mark.asyncio
async def test_gate_blocks_when_over_cap():
    storage = AsyncMock()
    storage.monthly_spend_cents = AsyncMock(return_value=9900.0)  # $99 spent
    storage.record_cost = AsyncMock()
    gate = BudgetGate(
        storage=storage, job_id="j1",
        block_on_exceed=True, monthly_cap_cents=10000,
    )
    allowed = await gate.check_and_record(
        CostRecord(
            model="claude-sonnet-4-6",
            input_tokens=200_000, output_tokens=50_000,  # ~135 cents
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
        agent="kai",
    )
    assert allowed is False
