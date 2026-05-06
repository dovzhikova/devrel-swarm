"""Tests for the pillar-filtered calibration helper."""

import sqlite3
from pathlib import Path

import pytest

from devrel_swarm.core.growth.recommendations import (
    Recommendation,
    calibrate,
    persist_recommendation,
)
from devrel_swarm.core.growth.target_kinds import Pillar, TargetKind
from devrel_swarm.project import state


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    state.init_db(db_path)
    return db_path


@pytest.fixture
def report_id(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-03-25", "2026-04-01", "{}"),
        )
        conn.commit()
        return cur.lastrowid


class TestCalibrate:
    def test_per_action_hit_rate(self, db: Path, report_id: int):
        """Two double_down recs, one applied: hit rate 1.0 (only applied counts)."""
        rec1 = Recommendation(
            pillar=Pillar.SEO,
            action="double_down",
            target="/a",
            target_kind=TargetKind.URL,
            confidence=0.9,
            source_ids=[],
            first_seen_period="2026-03-01",
            applied_at="2026-03-08T00:00:00",
        )
        rec2 = Recommendation(
            pillar=Pillar.SEO,
            action="double_down",
            target="/b",
            target_kind=TargetKind.URL,
            confidence=0.8,
            source_ids=[],
            first_seen_period="2026-03-01",
            applied_at=None,
        )
        persist_recommendation(db, report_id, rec1)
        persist_recommendation(db, report_id, rec2)

        def fake_outcome_scorer(rec: Recommendation) -> str:
            return "improved"

        result = calibrate(db, Pillar.SEO, outcome_scorer=fake_outcome_scorer)
        assert result["double_down"]["applied_count"] == 1
        assert result["double_down"]["hit_rate"] == 1.0

    def test_pillar_filter(self, db: Path, report_id: int):
        """Calibration filters by pillar: SEO recs do not show up in CRO calibration."""
        seo_rec = Recommendation(
            pillar=Pillar.SEO,
            action="double_down",
            target="/a",
            target_kind=TargetKind.URL,
            confidence=0.9,
            source_ids=[],
            first_seen_period="2026-03-01",
            applied_at="2026-03-08T00:00:00",
        )
        persist_recommendation(db, report_id, seo_rec)

        cro_calibration = calibrate(db, Pillar.CRO, outcome_scorer=lambda r: "improved")
        assert cro_calibration == {}

    def test_lift_vs_coin_flip(self, db: Path, report_id: int):
        """3 of 4 applied recs improved: hit_rate=0.75; lift_vs_coinflip = 0.25."""
        for i in range(4):
            rec = Recommendation(
                pillar=Pillar.SEO,
                action="double_down",
                target=f"/p{i}",
                target_kind=TargetKind.URL,
                confidence=0.8,
                source_ids=[],
                first_seen_period="2026-03-01",
                applied_at=f"2026-03-{i + 8:02d}T00:00:00",
            )
            persist_recommendation(db, report_id, rec)

        outcomes_iter = iter(["improved", "improved", "improved", "regressed"])
        result = calibrate(db, Pillar.SEO, outcome_scorer=lambda r: next(outcomes_iter))
        assert result["double_down"]["applied_count"] == 4
        assert result["double_down"]["hit_rate"] == 0.75
        assert result["double_down"]["lift_vs_coinflip"] == pytest.approx(0.25)
