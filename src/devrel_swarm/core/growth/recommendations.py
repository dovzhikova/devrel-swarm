"""Pillar-agnostic Recommendation dataclass + persistence + lifecycle queries.

This module is the contract every Growth-pipeline auditor (and Argus) writes
through. Each pillar produces `Recommendation` instances and calls
`persist_recommendation` to land them in `analytics_recommendations`. Lifecycle
helpers (`find_open_by_target`, `mark_applied`, `find_stale`) drive the
recommendation closed-loop that Mox consumes for brief generation.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from devrel_swarm.core.growth.target_kinds import (
    Pillar,
    TargetKind,
    validate_target_kind_for_pillar,
)


@dataclass
class Recommendation:
    """A single structured action recommendation emitted by a Growth auditor.

    Maps 1:1 to a row in `analytics_recommendations`.
    """

    pillar: Pillar
    action: str
    target: str
    target_kind: TargetKind
    confidence: float
    source_ids: list[str]
    first_seen_period: str
    applied_at: Optional[str] = None
    rationale: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.pillar, str):
            self.pillar = Pillar(self.pillar)
        if isinstance(self.target_kind, str):
            self.target_kind = TargetKind(self.target_kind)


def persist_recommendation(
    db_path: Path, report_id: int, rec: Recommendation
) -> None:
    """Insert a Recommendation into `analytics_recommendations`.

    Validates `(pillar, target_kind)` before INSERT: accidental cross-pillar
    target_kinds are caught here, not at calibration time.
    """
    validate_target_kind_for_pillar(rec.pillar, rec.target_kind)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analytics_recommendations
                (report_id, period_end, action, target, target_type, rationale,
                 confidence, source_ids_json, first_seen_period, applied_at,
                 pillar, target_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                rec.first_seen_period,
                rec.action,
                rec.target,
                rec.target_kind.value,
                rec.rationale or "",
                rec.confidence,
                json.dumps(rec.source_ids),
                rec.first_seen_period,
                rec.applied_at,
                rec.pillar.value,
                rec.target_kind.value,
            ),
        )
        conn.commit()


def _row_to_recommendation(row: tuple) -> Recommendation:
    return Recommendation(
        pillar=Pillar(row[0]),
        action=row[1],
        target=row[2],
        target_kind=TargetKind(row[3]),
        confidence=row[4],
        source_ids=json.loads(row[5] or "[]"),
        first_seen_period=row[6],
        applied_at=row[7],
        rationale=row[8] if len(row) > 8 else None,
    )


def find_open_by_target(db_path: Path, pillar: Pillar) -> list[Recommendation]:
    """Return all unapplied recommendations for a pillar, newest-first."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at, rationale
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NULL
            ORDER BY first_seen_period DESC
            """,
            (pillar.value,),
        )
        return [_row_to_recommendation(row) for row in cur.fetchall()]


def mark_applied(
    db_path: Path,
    pillar: Pillar,
    *,
    action: str,
    target: str,
    target_kind: TargetKind,
) -> None:
    """Stamp a recommendation as applied (Mox shipped the change)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE analytics_recommendations
               SET applied_at = datetime('now')
             WHERE pillar = ? AND action = ? AND target = ? AND target_kind = ?
               AND applied_at IS NULL
            """,
            (pillar.value, action, target, target_kind.value),
        )
        conn.commit()


def find_stale(
    db_path: Path,
    pillar: Pillar,
    *,
    current_period: str,
    stale_after_periods: int = 2,
) -> list[Recommendation]:
    """Return open recommendations whose first_seen_period is N+ periods old.

    `period` here is calendar weeks; `stale_after_periods=2` means
    "first_seen at least 14 days before current_period" qualifies as stale.
    """
    cutoff = date.fromisoformat(current_period) - timedelta(weeks=stale_after_periods)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at, rationale
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NULL
              AND first_seen_period <= ?
            ORDER BY first_seen_period ASC
            """,
            (pillar.value, cutoff.isoformat()),
        )
        return [_row_to_recommendation(row) for row in cur.fetchall()]


def calibrate(
    db_path: Path,
    pillar: Pillar,
    *,
    outcome_scorer: Callable[[Recommendation], str],
) -> dict[str, dict[str, float | int]]:
    """Per-action hit-rate calibration for one pillar's applied recommendations.

    `outcome_scorer(rec)` returns one of {'improved', 'unchanged', 'regressed'}.
    Each pillar implements its own scorer based on subsequent fact-table rows
    (for example, SEO checks if keyword position improved; CRO checks if
    conversion rate rose). This helper just aggregates.

    Returns: {action: {applied_count, hit_rate, lift_vs_coinflip,
                       avg_confidence, high_conf_hit_rate}}
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at, rationale
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NOT NULL
            """,
            (pillar.value,),
        )
        rows = cur.fetchall()

    by_action: dict[str, list[tuple[Recommendation, str]]] = defaultdict(list)
    for row in rows:
        rec = _row_to_recommendation(row)
        outcome = outcome_scorer(rec)
        by_action[rec.action].append((rec, outcome))

    result: dict[str, dict[str, float | int]] = {}
    for action, items in by_action.items():
        n = len(items)
        improved = sum(1 for _, o in items if o == "improved")
        hit_rate = improved / n if n else 0.0
        avg_conf = sum(r.confidence for r, _ in items) / n if n else 0.0
        # high-conf = top half by confidence
        sorted_items = sorted(items, key=lambda t: t[0].confidence, reverse=True)
        high_half = sorted_items[: max(1, n // 2)]
        high_improved = sum(1 for _, o in high_half if o == "improved")
        high_hit = high_improved / len(high_half) if high_half else 0.0

        result[action] = {
            "applied_count": n,
            "hit_rate": hit_rate,
            "lift_vs_coinflip": hit_rate - 0.5,
            "avg_confidence": avg_conf,
            "high_conf_hit_rate": high_hit,
        }
    return result
