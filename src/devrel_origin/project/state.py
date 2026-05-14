"""Project state DB: SQLite at .devrel/state.db.

Stores: jobs (kind, status, started/finished timestamps), costs (per-call
token + USD ledger consumed by BudgetGate), checkpoints (per-agent context
snapshots, one per (agent, week_of) pair).

In Phase 2 the DB is initialized empty by `devrel init`. Agents start
writing to it in Phase 3 (quality pipeline cost recording) and beyond.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    week_of TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, week_of)
);

CREATE TABLE IF NOT EXISTS analytics_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_reports_period
    ON analytics_reports(period_end);

CREATE TABLE IF NOT EXISTS metric_history (
    content_id TEXT NOT NULL,
    period_end TEXT NOT NULL,
    primary_metric REAL NOT NULL,
    metric_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    PRIMARY KEY (content_id, period_end)
);

CREATE INDEX IF NOT EXISTS idx_metric_history_content_period
    ON metric_history(content_id, period_end DESC);

CREATE TABLE IF NOT EXISTS analytics_recommendations (
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

CREATE INDEX IF NOT EXISTS idx_analytics_recommendations_period
    ON analytics_recommendations(period_end);

CREATE INDEX IF NOT EXISTS idx_analytics_recommendations_action_open
    ON analytics_recommendations(action, applied_at);

CREATE TABLE IF NOT EXISTS seo_keyword_metrics (
    keyword TEXT NOT NULL,
    page_url TEXT NOT NULL,
    period_end TEXT NOT NULL,
    position REAL,
    ctr REAL,
    impressions INTEGER,
    clicks INTEGER,
    PRIMARY KEY (keyword, page_url, period_end)
);

CREATE INDEX IF NOT EXISTS idx_seo_keyword_metrics_period
    ON seo_keyword_metrics(period_end DESC);

CREATE TABLE IF NOT EXISTS seo_page_profiles (
    page_url TEXT NOT NULL,
    period_end TEXT NOT NULL,
    title_len INTEGER,
    meta_len INTEGER,
    h1_count INTEGER,
    word_count INTEGER,
    has_schema INTEGER,
    schema_types_json TEXT,
    internal_links INTEGER,
    inp_ms INTEGER,
    lcp_ms INTEGER,
    redirect_chain_len INTEGER,
    crawled_at TEXT NOT NULL,
    PRIMARY KEY (page_url, period_end)
);

CREATE TABLE IF NOT EXISTS geo_visibility (
    prompt_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    period_end TEXT NOT NULL,
    is_mentioned INTEGER,
    mention_type TEXT,
    position_score INTEGER,
    citation_share REAL,
    quality_score INTEGER,
    response_path TEXT,
    PRIMARY KEY (prompt_id, engine, period_end)
);

CREATE INDEX IF NOT EXISTS idx_geo_visibility_engine_period
    ON geo_visibility(engine, period_end DESC);

CREATE TABLE IF NOT EXISTS cro_funnel_metrics (
    funnel_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    conversion_rate REAL,
    sample_size INTEGER,
    segment_breakdown_json TEXT,
    PRIMARY KEY (funnel_id, step_index, period_end)
);

CREATE INDEX IF NOT EXISTS idx_cro_funnel_period
    ON cro_funnel_metrics(funnel_id, period_end DESC);

CREATE TABLE IF NOT EXISTS social_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    url TEXT,
    author TEXT,
    content TEXT,
    sentiment TEXT,
    posted_at TEXT NOT NULL,
    subreddit TEXT,
    upvotes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    engagement_score REAL NOT NULL DEFAULT 0,
    is_own_post INTEGER NOT NULL DEFAULT 0,
    is_question INTEGER NOT NULL DEFAULT 0,
    requires_response INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, post_id)
);

CREATE INDEX IF NOT EXISTS idx_social_mentions_posted_at
    ON social_mentions(posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_social_mentions_own_period
    ON social_mentions(is_own_post, posted_at DESC);
"""


_SOCIAL_MENTIONS_COLUMNS: dict[str, str] = {
    "platform": "TEXT NOT NULL DEFAULT ''",
    "post_id": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT",
    "url": "TEXT",
    "author": "TEXT",
    "content": "TEXT",
    "sentiment": "TEXT",
    "posted_at": "TEXT NOT NULL DEFAULT ''",
    "subreddit": "TEXT",
    "upvotes": "INTEGER NOT NULL DEFAULT 0",
    "comments": "INTEGER NOT NULL DEFAULT 0",
    "engagement_score": "REAL NOT NULL DEFAULT 0",
    "is_own_post": "INTEGER NOT NULL DEFAULT 0",
    "is_question": "INTEGER NOT NULL DEFAULT 0",
    "requires_response": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Add pillar + target_kind columns to analytics_recommendations if absent.

    SQLite's ALTER TABLE ADD COLUMN is non-idempotent: running twice raises
    OperationalError. We probe PRAGMA table_info first.
    """
    cur = conn.execute("PRAGMA table_info(analytics_recommendations)")
    cols = {row[1] for row in cur.fetchall()}
    if "pillar" not in cols:
        conn.execute(
            "ALTER TABLE analytics_recommendations ADD COLUMN pillar TEXT NOT NULL DEFAULT 'argus'"
        )
    if "target_kind" not in cols:
        conn.execute(
            "ALTER TABLE analytics_recommendations "
            "ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'content_id'"
        )
    # Backfill any rows that pre-date these columns. DEFAULT covers fresh
    # inserts but old rows from a partial v4 db benefit from explicit values.
    conn.execute(
        "UPDATE analytics_recommendations "
        "SET pillar = COALESCE(NULLIF(pillar, ''), 'argus'), "
        "    target_kind = COALESCE(NULLIF(target_kind, ''), 'content_id') "
        "WHERE pillar IS NULL OR pillar = '' "
        "   OR target_kind IS NULL OR target_kind = ''"
    )
    # Indexes that reference the newly-added columns. Created here (not in
    # SCHEMA) because SCHEMA's executescript runs before the ALTER TABLEs above.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recs_pillar_period "
        "ON analytics_recommendations(pillar, first_seen_period DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recs_target "
        "ON analytics_recommendations(target_kind, target)"
    )


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Ensure Echo/Argus social mention storage exists and has v6 columns."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS social_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            title TEXT,
            url TEXT,
            author TEXT,
            content TEXT,
            sentiment TEXT,
            posted_at TEXT NOT NULL,
            subreddit TEXT,
            upvotes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            engagement_score REAL NOT NULL DEFAULT 0,
            is_own_post INTEGER NOT NULL DEFAULT 0,
            is_question INTEGER NOT NULL DEFAULT 0,
            requires_response INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (platform, post_id)
        )
        """
    )
    cur = conn.execute("PRAGMA table_info(social_mentions)")
    cols = {row[1] for row in cur.fetchall()}
    for col, ddl in _SOCIAL_MENTIONS_COLUMNS.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE social_mentions ADD COLUMN {col} {ddl}")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(social_mentions)").fetchall()}
    if "score" in cols:
        conn.execute(
            "UPDATE social_mentions "
            "SET engagement_score = COALESCE(NULLIF(engagement_score, 0), score, 0)"
        )
    if "engagement" in cols:
        conn.execute(
            "UPDATE social_mentions "
            "SET engagement_score = COALESCE(NULLIF(engagement_score, 0), engagement, 0), "
            "    upvotes = COALESCE(NULLIF(upvotes, 0), engagement, 0)"
        )
    if "url" in cols:
        fallback_id = "CAST(id AS TEXT)" if "id" in cols else "rowid"
        conn.execute(
            "UPDATE social_mentions "
            f"SET post_id = COALESCE(NULLIF(post_id, ''), NULLIF(url, ''), {fallback_id}) "
            "WHERE post_id IS NULL OR post_id = ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_mentions_posted_at "
        "ON social_mentions(posted_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_mentions_own_period "
        "ON social_mentions(is_own_post, posted_at DESC)"
    )


def init_db(db_path: Path) -> None:
    """Create the DB file and apply the schema. Idempotent: preserves
    existing data and bumps schema_meta to the current SCHEMA_VERSION."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_to_v5(conn)
        _migrate_to_v6(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (version, applied_at) VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        conn.commit()


def get_schema_version(db_path: Path) -> int | None:
    """Return the current schema version, or None if the DB is missing /
    has no schema_meta table."""
    if not db_path.is_file():
        return None
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
        except sqlite3.OperationalError:
            return None
        row = cur.fetchone()
        return row[0] if row else None


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection with row_factory set and
    foreign-key enforcement enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
