"""State schema migration tests for growth tables and social mentions."""

import sqlite3
from pathlib import Path

from devrel_swarm.project import state


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


class TestSchemaV5:
    def test_schema_version_is_6(self):
        assert state.SCHEMA_VERSION == 6

    def test_init_creates_seo_keyword_metrics(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "seo_keyword_metrics" in _tables(conn)
            cols = _columns(conn, "seo_keyword_metrics")
            assert {
                "keyword",
                "page_url",
                "period_end",
                "position",
                "ctr",
                "impressions",
                "clicks",
            } <= cols

    def test_init_creates_seo_page_profiles(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "seo_page_profiles" in _tables(conn)
            cols = _columns(conn, "seo_page_profiles")
            assert {
                "page_url",
                "period_end",
                "title_len",
                "meta_len",
                "h1_count",
                "word_count",
                "has_schema",
                "schema_types_json",
                "internal_links",
                "inp_ms",
                "lcp_ms",
                "redirect_chain_len",
                "crawled_at",
            } <= cols

    def test_init_creates_geo_visibility(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "geo_visibility" in _tables(conn)
            cols = _columns(conn, "geo_visibility")
            assert {
                "prompt_id",
                "engine",
                "period_end",
                "is_mentioned",
                "mention_type",
                "position_score",
                "citation_share",
                "quality_score",
                "response_path",
            } <= cols

    def test_init_creates_cro_funnel_metrics(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "cro_funnel_metrics" in _tables(conn)
            cols = _columns(conn, "cro_funnel_metrics")
            assert {
                "funnel_id",
                "step_index",
                "period_end",
                "conversion_rate",
                "sample_size",
                "segment_breakdown_json",
            } <= cols

    def test_init_creates_social_mentions(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "social_mentions" in _tables(conn)
            cols = _columns(conn, "social_mentions")
            assert {
                "platform",
                "post_id",
                "title",
                "url",
                "posted_at",
                "upvotes",
                "comments",
                "engagement_score",
                "is_own_post",
            } <= cols


class TestPillarColumns:
    def test_init_adds_pillar_column(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "pillar" in _columns(conn, "analytics_recommendations")

    def test_init_adds_target_kind_column(self, tmp_path: Path):
        db = tmp_path / "state.db"
        state.init_db(db)
        with sqlite3.connect(db) as conn:
            assert "target_kind" in _columns(conn, "analytics_recommendations")

    def test_existing_v4_db_migrates_to_v5(self, tmp_path: Path):
        """Simulate an existing v4 database (with analytics_recommendations
        missing the new columns) and assert init_db migrates it cleanly."""
        db = tmp_path / "state.db"
        # Hand-build a v4-shaped database (matches the v4 SCHEMA exactly)
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE schema_meta (version INTEGER, applied_at TEXT);
                INSERT INTO schema_meta VALUES (4, datetime('now'));
                CREATE TABLE analytics_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO analytics_reports (id, period_start, period_end, report_json)
                    VALUES (1, '2026-03-25', '2026-04-01', '{}');
                CREATE TABLE analytics_recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL,
                    period_end TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    first_seen_period TEXT NOT NULL,
                    applied_at TEXT,
                    FOREIGN KEY (report_id) REFERENCES analytics_reports(id) ON DELETE CASCADE
                );
                INSERT INTO analytics_recommendations
                    (report_id, period_end, action, target, target_type, rationale,
                     confidence, source_ids_json, first_seen_period)
                VALUES (1, '2026-04-01', 'double_down', 'doc-quickstart',
                        'content_id', 'High engagement', 0.8, '["c1"]', '2026-04-01');
            """)

        # Run init_db, should migrate to v5
        state.init_db(db)

        with sqlite3.connect(db) as conn:
            cols = _columns(conn, "analytics_recommendations")
            assert "pillar" in cols
            assert "target_kind" in cols
            # Backfill: existing row should now have pillar='argus', target_kind='content_id'
            cur = conn.execute(
                "SELECT pillar, target_kind FROM analytics_recommendations "
                "WHERE report_id=1 AND target='doc-quickstart'"
            )
            row = cur.fetchone()
            assert row == ("argus", "content_id")
            # Schema version bumped
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
            assert cur.fetchone()[0] == state.SCHEMA_VERSION

    def test_migration_is_idempotent(self, tmp_path: Path):
        """Running init_db twice on a v5 database is a no-op, not a crash."""
        db = tmp_path / "state.db"
        state.init_db(db)
        state.init_db(db)  # second call must not fail
        with sqlite3.connect(db) as conn:
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
            assert cur.fetchone()[0] == state.SCHEMA_VERSION

    def test_existing_partial_social_mentions_table_migrates_to_v6(self, tmp_path: Path):
        db = tmp_path / "state.db"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_meta (version INTEGER, applied_at TEXT);
                INSERT INTO schema_meta VALUES (5, datetime('now'));
                CREATE TABLE social_mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    post_id TEXT NOT NULL,
                    title TEXT,
                    url TEXT,
                    posted_at TEXT NOT NULL,
                    upvotes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    score REAL DEFAULT 0,
                    is_own_post INTEGER DEFAULT 0
                );
                INSERT INTO social_mentions
                    (platform, post_id, title, url, posted_at, upvotes, comments, score, is_own_post)
                VALUES
                    ('reddit', 'abc', 'Launch', 'https://reddit.com/abc',
                     '2026-05-01T00:00:00+00:00', 10, 2, 12.5, 1);
                """
            )

        state.init_db(db)

        with sqlite3.connect(db) as conn:
            cols = _columns(conn, "social_mentions")
            assert {
                "engagement_score",
                "author",
                "content",
                "sentiment",
                "is_question",
                "requires_response",
            } <= cols
            row = conn.execute(
                "SELECT engagement_score FROM social_mentions WHERE post_id = 'abc'"
            ).fetchone()
            assert row == (12.5,)
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
            assert cur.fetchone()[0] == state.SCHEMA_VERSION
