"""Schema v5 migration tests for Growth pillar fact tables + ALTER on analytics_recommendations."""

import sqlite3
from pathlib import Path

import pytest

from devrel_swarm.project import state


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


class TestSchemaV5:
    def test_schema_version_is_5(self):
        assert state.SCHEMA_VERSION == 5

    def test_init_creates_seo_keyword_metrics(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "seo_keyword_metrics" in _tables(conn)
            cols = _columns(conn, "seo_keyword_metrics")
            assert {"keyword", "page_url", "period_end", "position", "ctr", "impressions", "clicks"} <= cols

    def test_init_creates_seo_page_profiles(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "seo_page_profiles" in _tables(conn)
            cols = _columns(conn, "seo_page_profiles")
            assert {
                "page_url", "period_end", "title_len", "meta_len", "h1_count",
                "word_count", "has_schema", "schema_types_json", "internal_links",
                "inp_ms", "lcp_ms", "redirect_chain_len", "crawled_at",
            } <= cols

    def test_init_creates_geo_visibility(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "geo_visibility" in _tables(conn)
            cols = _columns(conn, "geo_visibility")
            assert {"prompt_id", "engine", "period_end", "is_mentioned", "mention_type",
                    "position_score", "citation_share", "quality_score", "response_path"} <= cols

    def test_init_creates_cro_funnel_metrics(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "cro_funnel_metrics" in _tables(conn)
            cols = _columns(conn, "cro_funnel_metrics")
            assert {"funnel_id", "step_index", "period_end", "conversion_rate",
                    "sample_size", "segment_breakdown_json"} <= cols
