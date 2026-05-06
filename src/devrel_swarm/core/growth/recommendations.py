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
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

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
