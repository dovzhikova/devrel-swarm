"""Tests for the pillar-agnostic Recommendation persistence layer."""

import json
import sqlite3
from pathlib import Path

import pytest

from devrel_swarm.core.growth.recommendations import (
    Recommendation,
    persist_recommendation,
    find_open_by_target,
    mark_applied,
    find_stale,
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
    """Insert a parent analytics_reports row so report_id FKs are satisfied."""
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-03-25", "2026-04-01", "{}"),
        )
        conn.commit()
        return cur.lastrowid


class TestPersist:
    def test_persist_inserts_row(self, db: Path, report_id: int):
        rec = Recommendation(
            pillar=Pillar.SEO,
            action="rewrite",
            target="https://example.com/docs",
            target_kind=TargetKind.URL,
            confidence=0.85,
            source_ids=["page-001"],
            first_seen_period="2026-04-01",
        )
        persist_recommendation(db, report_id, rec)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT pillar, action, target, target_kind, confidence, "
                "       source_ids_json, first_seen_period "
                "FROM analytics_recommendations WHERE report_id = ?",
                (report_id,),
            )
            row = cur.fetchone()
            assert row[0] == "seo"
            assert row[1] == "rewrite"
            assert row[2] == "https://example.com/docs"
            assert row[3] == "url"
            assert row[4] == 0.85
            assert json.loads(row[5]) == ["page-001"]
            assert row[6] == "2026-04-01"

    def test_persist_validates_target_kind(self, db: Path, report_id: int):
        rec = Recommendation(
            pillar=Pillar.CRO,
            action="retest",
            target="signup_started",
            target_kind=TargetKind.URL,  # invalid for CRO
            confidence=0.7,
            source_ids=[],
            first_seen_period="2026-04-01",
        )
        with pytest.raises(ValueError, match="not valid for pillar"):
            persist_recommendation(db, report_id, rec)


class TestFindOpenByTarget:
    def test_returns_unapplied_only(self, db: Path, report_id: int):
        rec1 = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/a", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-04-01",
        )
        rec2 = Recommendation(
            pillar=Pillar.SEO, action="amplify", target="/b", target_kind=TargetKind.URL,
            confidence=0.8, source_ids=[], first_seen_period="2026-04-01",
        )
        persist_recommendation(db, report_id, rec1)
        persist_recommendation(db, report_id, rec2)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE analytics_recommendations SET applied_at = datetime('now') "
                "WHERE target = '/a'"
            )
            conn.commit()

        open_recs = find_open_by_target(db, Pillar.SEO)
        assert len(open_recs) == 1
        assert open_recs[0].target == "/b"

    def test_filters_by_pillar(self, db: Path, report_id: int):
        seo_rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/a", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-04-01",
        )
        cro_rec = Recommendation(
            pillar=Pillar.CRO, action="retest", target="signup", target_kind=TargetKind.FUNNEL_STEP,
            confidence=0.8, source_ids=[], first_seen_period="2026-04-01",
        )
        persist_recommendation(db, report_id, seo_rec)
        persist_recommendation(db, report_id, cro_rec)

        seo_open = find_open_by_target(db, Pillar.SEO)
        cro_open = find_open_by_target(db, Pillar.CRO)
        assert len(seo_open) == 1
        assert len(cro_open) == 1
        assert seo_open[0].target == "/a"
        assert cro_open[0].target == "signup"


class TestMarkApplied:
    def test_mark_applied_sets_timestamp(self, db: Path, report_id: int):
        rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/x", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-04-01",
        )
        persist_recommendation(db, report_id, rec)

        mark_applied(db, Pillar.SEO, action="rewrite", target="/x", target_kind=TargetKind.URL)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT applied_at FROM analytics_recommendations WHERE target = '/x'"
            )
            assert cur.fetchone()[0] is not None


class TestFindStale:
    def test_stale_returns_recs_older_than_n_periods(self, db: Path, report_id: int):
        rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/old", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-01",
        )
        persist_recommendation(db, report_id, rec)

        stale = find_stale(db, Pillar.SEO, current_period="2026-04-01", stale_after_periods=2)
        assert len(stale) == 1
        assert stale[0].target == "/old"

    def test_stale_excludes_recent(self, db: Path, report_id: int):
        rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/new", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-25",
        )
        persist_recommendation(db, report_id, rec)

        stale = find_stale(db, Pillar.SEO, current_period="2026-04-01", stale_after_periods=2)
        assert len(stale) == 0
