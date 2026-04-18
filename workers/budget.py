"""Cost tracking + budget enforcement gate.

v0: tracking-only (block_on_exceed=False) — runs inside each instance.
v1: block_on_exceed=True enforces monthly_cap_cents set at provision time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

# Anthropic pricing, $ per 1M tokens. Update when pricing changes.
# Source: https://www.anthropic.com/pricing (verified 2026-04-18)
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0, "output": 75.0,
        "cache_write": 18.75, "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.0, "output": 5.0,
        "cache_write": 1.25, "cache_read": 0.10,
    },
}

_MODEL_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _normalize_model(model: str) -> str:
    """Strip trailing -YYYYMMDD date suffix so dated SDK responses match pricing keys."""
    return _MODEL_DATE_SUFFIX.sub("", model)


class CostStorage(Protocol):
    async def monthly_spend_cents(self) -> float: ...
    async def record_cost(
        self,
        job_id: str | None,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        cost_cents: float,
    ) -> None: ...


@dataclass
class CostRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int

    @property
    def cost_cents(self) -> float:
        prices = _PRICING.get(_normalize_model(self.model))
        if prices is None:
            raise ValueError(f"unknown model for pricing: {self.model}")
        dollars = (
            self.input_tokens * prices["input"] / 1_000_000
            + self.output_tokens * prices["output"] / 1_000_000
            + self.cache_creation_input_tokens * prices["cache_write"] / 1_000_000
            + self.cache_read_input_tokens * prices["cache_read"] / 1_000_000
        )
        return dollars * 100


class BudgetExceeded(RuntimeError):
    pass


class BudgetGate:
    def __init__(
        self,
        storage: CostStorage,
        job_id: str | None,
        block_on_exceed: bool = False,
        monthly_cap_cents: int = 0,
    ) -> None:
        if block_on_exceed and monthly_cap_cents <= 0:
            raise ValueError(
                "block_on_exceed=True requires monthly_cap_cents > 0"
            )
        self._storage = storage
        self._job_id = job_id
        self._block = block_on_exceed
        self._cap = monthly_cap_cents

    async def check_and_record(self, rec: CostRecord, agent: str) -> bool:
        if self._block and self._cap > 0:
            current = await self._storage.monthly_spend_cents()
            if current + rec.cost_cents > self._cap:
                logger.warning(
                    "BudgetGate blocked agent=%s projected=%.2f cap=%d",
                    agent, current + rec.cost_cents, self._cap,
                )
                return False

        await self._storage.record_cost(
            job_id=self._job_id, agent=agent, model=rec.model,
            input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
            cache_creation_input_tokens=rec.cache_creation_input_tokens,
            cache_read_input_tokens=rec.cache_read_input_tokens,
            cost_cents=rec.cost_cents,
        )
        return True
