# Growth Pipeline Wave 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the schema, shared modules, and CLI umbrella that the three Growth pillars (Cyra/Vega/Selene) will build on top of. No agents land in this wave — only the foundation that lets them share `analytics_recommendations`, calibration, and brief generation.

**Architecture:** Schema v5 ALTER TABLE on Argus's `analytics_recommendations` (adds `pillar` + `target_kind` columns) plus three new per-pillar fact tables (`seo_keyword_metrics`, `seo_page_profiles`, `geo_visibility`, `cro_funnel_metrics`). New shared module `core/growth/` extracts Argus's `_persist`, lifecycle queries, and calibration scoring into pillar-agnostic helpers. New CLI namespace `cli/growth.py` for cross-pillar `summary`/`diff` verbs. Existing `cli/analytics.py` is renamed to `cli/argus.py` with a backward-compat alias.

**Tech Stack:** Python 3.12 async, `sqlite3` via existing `project/state.py` helpers, `dataclasses`, Typer CLI, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-05-growth-pipeline-design.md`

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_swarm/project/state.py` | Modify | Bump `SCHEMA_VERSION` to 5; add `pillar` + `target_kind` ALTERs via `_migrate_to_v5`; add 4 new fact tables to `SCHEMA` constant; add 2 new indexes |
| `src/devrel_swarm/core/growth/__init__.py` | Create | Public exports for `Recommendation`, `TargetKind`, `Pillar`, `persist_recommendation`, `find_open_by_target`, `mark_applied`, `find_stale`, `calibrate` |
| `src/devrel_swarm/core/growth/target_kinds.py` | Create | `TargetKind` enum + `Pillar` enum + collision-guard validator |
| `src/devrel_swarm/core/growth/recommendations.py` | Create | Pillar-agnostic `Recommendation` dataclass; `persist_recommendation`, `find_open_by_target`, `mark_applied`, `find_stale` queries; `calibrate` helper |
| `src/devrel_swarm/core/argus.py` | Modify | Use shared `growth.persist_recommendation` + `growth.calibrate` instead of inline; pass `pillar="argus"`, `target_kind="content_id"` |
| `src/devrel_swarm/cli/growth.py` | Create | Typer `growth_app` with `summary` + `diff` placeholder verbs |
| `src/devrel_swarm/cli/argus.py` | Create | Renamed copy of `cli/analytics.py` (Argus-only verbs) |
| `src/devrel_swarm/cli/analytics.py` | Modify | Becomes a deprecation-warning alias that delegates to `argus.argus_app` |
| `src/devrel_swarm/cli/__init__.py` | Modify | Register `growth_app`, `argus_app`; deprecation-alias `analytics_app` |
| `pyproject.toml` | Modify | Add `[seo]`, `[geo-google]`, `[growth]` optional-dependencies extras; update `[dev]` to pull `[video,growth]` |
| `tests/project/test_state_v5_migration.py` | Create | Migrate v4 dump → v5; assert columns present + indexes created + idempotent on second run |
| `tests/core/growth/__init__.py` | Create | Empty package marker |
| `tests/core/growth/test_target_kinds.py` | Create | Pillar/TargetKind enum + collision tests |
| `tests/core/growth/test_recommendations.py` | Create | persist/find/mark/find_stale unit tests |
| `tests/core/growth/test_calibration.py` | Create | Per-pillar calibration filter tests |
| `tests/cli/test_growth_command.py` | Create | growth umbrella CLI smoke tests |
| `tests/cli/test_analytics_alias.py` | Create | `devrel analytics ...` → `argus` alias deprecation-warning test |
| `docs/setup-google-oauth.md` | Create | Manual walkthrough for Daria to configure the GCP project + OAuth consent + verification submission |

**Out of scope for Wave 0:** any agent code (Selene/Vega/Cyra) — they land in Waves 1-3. Atlas integration — lands in Wave 4.

---

## Task 1: Bump `SCHEMA_VERSION` to 5 + add new fact tables to `SCHEMA`

**Files:**
- Modify: `src/devrel_swarm/project/state.py`
- Test: `tests/project/test_state_v5_migration.py`

- [ ] **Step 1: Write the failing test that asserts `SCHEMA_VERSION == 5` and the new tables exist after init**

Create `tests/project/test_state_v5_migration.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/devrel-swarm && source .venv/bin/activate
pytest tests/project/test_state_v5_migration.py::TestSchemaV5 -v --no-cov
```

Expected: 5 FAILED. `SCHEMA_VERSION == 4`, new tables don't exist.

- [ ] **Step 3: Bump version + add tables to SCHEMA constant**

Edit `src/devrel_swarm/project/state.py`:

```python
# Change line 18:
SCHEMA_VERSION = 5

# Inside the SCHEMA = """...""" string (before the closing """),
# AFTER the existing analytics_recommendations indexes, ADD:

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
    schema_types_json TEXT,        -- JSON array of detected schema types
    internal_links INTEGER,
    inp_ms INTEGER,                -- Core Web Vitals 2.0 (PageSpeed Insights)
    lcp_ms INTEGER,                -- Core Web Vitals 2.0
    redirect_chain_len INTEGER,    -- # redirects before reaching the URL
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/project/test_state_v5_migration.py::TestSchemaV5 -v --no-cov
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/project/state.py tests/project/test_state_v5_migration.py
git commit -m "feat(schema): v5 — add SEO/GEO/CRO fact tables (Wave 0/1)"
```

---

## Task 2: ALTER `analytics_recommendations` for `pillar` + `target_kind`

**Files:**
- Modify: `src/devrel_swarm/project/state.py`
- Test: `tests/project/test_state_v5_migration.py` (extend)

- [ ] **Step 1: Write the failing test that asserts pillar + target_kind columns exist after init**

Append to `tests/project/test_state_v5_migration.py`:

```python
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
        # Hand-build a v4-shaped database
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE schema_meta (version INTEGER, applied_at TEXT);
                INSERT INTO schema_meta VALUES (4, datetime('now'));
                CREATE TABLE analytics_recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    source_ids_json TEXT,
                    confidence REAL,
                    first_seen_period TEXT,
                    applied_at TEXT
                );
                INSERT INTO analytics_recommendations
                    (report_id, action, target, source_ids_json, confidence, first_seen_period)
                VALUES ('r1', 'double_down', 'doc-quickstart', '["c1"]', 0.8, '2026-04-01');
            """)

        # Run init_db — should migrate to v5
        state.init_db(db)

        with sqlite3.connect(db) as conn:
            cols = _columns(conn, "analytics_recommendations")
            assert "pillar" in cols
            assert "target_kind" in cols
            # Backfill: existing row should now have pillar='argus', target_kind='content_id'
            cur = conn.execute(
                "SELECT pillar, target_kind FROM analytics_recommendations WHERE report_id='r1'"
            )
            row = cur.fetchone()
            assert row == ("argus", "content_id")
            # Schema version bumped
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
            assert cur.fetchone()[0] == 5

    def test_migration_is_idempotent(self, tmp_path: Path):
        """Running init_db twice on a v5 database is a no-op, not a crash."""
        db = tmp_path / "state.db"
        state.init_db(db)
        state.init_db(db)  # second call must not fail
        with sqlite3.connect(db) as conn:
            cur = conn.execute("SELECT MAX(version) FROM schema_meta")
            assert cur.fetchone()[0] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/project/test_state_v5_migration.py::TestPillarColumns -v --no-cov
```

Expected: 4 FAILED. Columns missing.

- [ ] **Step 3: Add the migration function**

Edit `src/devrel_swarm/project/state.py`. Add this helper above `init_db`:

```python
def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Add pillar + target_kind columns to analytics_recommendations if absent.

    SQLite's ALTER TABLE ADD COLUMN is non-idempotent — running twice raises
    OperationalError. We probe `PRAGMA table_info` first.
    """
    cur = conn.execute("PRAGMA table_info(analytics_recommendations)")
    cols = {row[1] for row in cur.fetchall()}
    if "pillar" not in cols:
        conn.execute(
            "ALTER TABLE analytics_recommendations "
            "ADD COLUMN pillar TEXT NOT NULL DEFAULT 'argus'"
        )
    if "target_kind" not in cols:
        conn.execute(
            "ALTER TABLE analytics_recommendations "
            "ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'content_id'"
        )
    # Backfill any rows that pre-date these columns (defensive — DEFAULT
    # already covers fresh inserts but old rows from a partial v4 db
    # benefit from explicit values).
    conn.execute(
        "UPDATE analytics_recommendations "
        "SET pillar = COALESCE(NULLIF(pillar, ''), 'argus'), "
        "    target_kind = COALESCE(NULLIF(target_kind, ''), 'content_id') "
        "WHERE pillar IS NULL OR pillar = '' "
        "   OR target_kind IS NULL OR target_kind = ''"
    )
```

Also append two indexes to the `SCHEMA` constant (after the new tables from Task 1):

```python
CREATE INDEX IF NOT EXISTS idx_recs_pillar_period
    ON analytics_recommendations(pillar, first_seen_period DESC);

CREATE INDEX IF NOT EXISTS idx_recs_target
    ON analytics_recommendations(target_kind, target);
```

Then modify `init_db` to call `_migrate_to_v5` after `executescript`:

```python
def init_db(db_path: Path) -> None:
    """Create the DB file and apply the schema. Idempotent — preserves
    existing data and bumps schema_meta to the current SCHEMA_VERSION."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_to_v5(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (version, applied_at) VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/project/test_state_v5_migration.py -v --no-cov
```

Expected: 9 PASSED (5 from Task 1 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/project/state.py tests/project/test_state_v5_migration.py
git commit -m "feat(schema): v5 — pillar + target_kind columns on analytics_recommendations"
```

---

## Task 3: `Pillar` and `TargetKind` enums

**Files:**
- Create: `src/devrel_swarm/core/growth/__init__.py`
- Create: `src/devrel_swarm/core/growth/target_kinds.py`
- Create: `tests/core/growth/__init__.py`
- Create: `tests/core/growth/test_target_kinds.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/growth/__init__.py` (empty).

Create `tests/core/growth/test_target_kinds.py`:

```python
"""Tests for Pillar + TargetKind enums and the (pillar, target_kind) collision guard."""

import pytest

from devrel_swarm.core.growth.target_kinds import (
    Pillar,
    TargetKind,
    validate_target_kind_for_pillar,
)


class TestPillar:
    def test_all_pillars_present(self):
        names = {p.value for p in Pillar}
        assert names == {"argus", "seo", "geo", "cro"}

    def test_pillar_lookup_by_value(self):
        assert Pillar("argus") == Pillar.ARGUS
        assert Pillar("seo") == Pillar.SEO


class TestTargetKind:
    def test_all_kinds_present(self):
        names = {k.value for k in TargetKind}
        assert names == {
            "content_id", "url", "keyword",
            "funnel_step", "brand_query", "competitor",
        }


class TestValidator:
    @pytest.mark.parametrize("pillar,kind", [
        (Pillar.ARGUS, TargetKind.CONTENT_ID),
        (Pillar.SEO, TargetKind.URL),
        (Pillar.SEO, TargetKind.KEYWORD),
        (Pillar.GEO, TargetKind.BRAND_QUERY),
        (Pillar.GEO, TargetKind.URL),
        (Pillar.GEO, TargetKind.COMPETITOR),
        (Pillar.CRO, TargetKind.FUNNEL_STEP),
    ])
    def test_valid_pairs(self, pillar: Pillar, kind: TargetKind):
        # Should not raise
        validate_target_kind_for_pillar(pillar, kind)

    @pytest.mark.parametrize("pillar,kind", [
        (Pillar.ARGUS, TargetKind.URL),       # Argus only writes content_id
        (Pillar.SEO, TargetKind.FUNNEL_STEP), # SEO never sees funnels
        (Pillar.GEO, TargetKind.CONTENT_ID),  # GEO has its own brand_query target
        (Pillar.CRO, TargetKind.KEYWORD),     # CRO never writes keywords
    ])
    def test_invalid_pairs_raise(self, pillar: Pillar, kind: TargetKind):
        with pytest.raises(ValueError, match="not valid for pillar"):
            validate_target_kind_for_pillar(pillar, kind)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/growth/test_target_kinds.py -v --no-cov
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Create `target_kinds.py`**

Create `src/devrel_swarm/core/growth/__init__.py`:

```python
"""Shared helpers for the Growth pipeline (Selene/Vega/Cyra + Argus).

Pillar-agnostic Recommendation persistence + lifecycle queries + calibration
math. Each pillar agent imports from here and contributes pillar-specific
scoring on top.
"""

from devrel_swarm.core.growth.target_kinds import (
    Pillar,
    TargetKind,
    validate_target_kind_for_pillar,
)

__all__ = [
    "Pillar",
    "TargetKind",
    "validate_target_kind_for_pillar",
]
```

Create `src/devrel_swarm/core/growth/target_kinds.py`:

```python
"""Pillar + TargetKind enums and the (pillar, target_kind) collision guard.

Stored as TEXT in SQLite (`analytics_recommendations.pillar` and
`.target_kind`) but typed at the Python boundary so accidental
free-form strings are caught at write time, not at calibration.
"""

from __future__ import annotations

from enum import Enum


class Pillar(str, Enum):
    ARGUS = "argus"
    SEO = "seo"
    GEO = "geo"
    CRO = "cro"


class TargetKind(str, Enum):
    CONTENT_ID = "content_id"     # Argus — content identifier
    URL = "url"                   # SEO + GEO — page URL
    KEYWORD = "keyword"           # SEO — search keyword
    FUNNEL_STEP = "funnel_step"   # CRO — funnel step name
    BRAND_QUERY = "brand_query"   # GEO — branded search prompt
    COMPETITOR = "competitor"     # GEO — competitor brand name


# Per-pillar allowlists. Cross-cutting kinds (URL is in both SEO + GEO)
# are the reason we don't just key off pillar alone in the schema.
_VALID: dict[Pillar, frozenset[TargetKind]] = {
    Pillar.ARGUS: frozenset({TargetKind.CONTENT_ID}),
    Pillar.SEO: frozenset({TargetKind.URL, TargetKind.KEYWORD}),
    Pillar.GEO: frozenset({TargetKind.BRAND_QUERY, TargetKind.URL, TargetKind.COMPETITOR}),
    Pillar.CRO: frozenset({TargetKind.FUNNEL_STEP}),
}


def validate_target_kind_for_pillar(pillar: Pillar, kind: TargetKind) -> None:
    """Raise ValueError if `kind` is not a legal target for `pillar`.

    Called by `persist_recommendation` before INSERT. Keeps the
    cross-pillar query namespace coherent: a `target_kind='url'` row
    is unambiguously SEO or GEO and never anything else.
    """
    if kind not in _VALID[pillar]:
        valid_names = sorted(k.value for k in _VALID[pillar])
        raise ValueError(
            f"target_kind={kind.value!r} not valid for pillar={pillar.value!r}; "
            f"valid kinds: {valid_names}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/growth/test_target_kinds.py -v --no-cov
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/growth/ tests/core/growth/__init__.py tests/core/growth/test_target_kinds.py
git commit -m "feat(growth): Pillar + TargetKind enums with per-pillar validator"
```

---

## Task 4: Pillar-agnostic `Recommendation` dataclass + `persist_recommendation`

**Files:**
- Create: `src/devrel_swarm/core/growth/recommendations.py`
- Test: `tests/core/growth/test_recommendations.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/growth/test_recommendations.py`:

```python
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
def report_id(db: Path) -> str:
    """Insert a fake report_id row so foreign-key-style refs are satisfied."""
    rid = "test-report-2026-04-01"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (id, period_end, generated_at, body_json) "
            "VALUES (?, ?, datetime('now'), '{}')",
            (rid, "2026-04-01"),
        )
        conn.commit()
    return rid


class TestPersist:
    def test_persist_inserts_row(self, db: Path, report_id: str):
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

    def test_persist_validates_target_kind(self, db: Path, report_id: str):
        # CRO with target_kind=url should raise
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
    def test_returns_unapplied_only(self, db: Path, report_id: str):
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
        # Mark first applied
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE analytics_recommendations SET applied_at = datetime('now') "
                "WHERE target = '/a'"
            )
            conn.commit()

        open_recs = find_open_by_target(db, Pillar.SEO)
        assert len(open_recs) == 1
        assert open_recs[0].target == "/b"

    def test_filters_by_pillar(self, db: Path, report_id: str):
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
    def test_mark_applied_sets_timestamp(self, db: Path, report_id: str):
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
    def test_stale_returns_recs_older_than_n_periods(self, db: Path, report_id: str):
        rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/old", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-01",
        )
        persist_recommendation(db, report_id, rec)

        stale = find_stale(db, Pillar.SEO, current_period="2026-04-01", stale_after_periods=2)
        assert len(stale) == 1
        assert stale[0].target == "/old"

    def test_stale_excludes_recent(self, db: Path, report_id: str):
        rec = Recommendation(
            pillar=Pillar.SEO, action="rewrite", target="/new", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-25",
        )
        persist_recommendation(db, report_id, rec)

        stale = find_stale(db, Pillar.SEO, current_period="2026-04-01", stale_after_periods=2)
        assert len(stale) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/growth/test_recommendations.py -v --no-cov
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Create the persistence module**

Create `src/devrel_swarm/core/growth/recommendations.py`:

```python
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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    action: str            # vocab is per-pillar (Argus's: double_down, retire, ...)
    target: str            # entity the action acts on (URL, keyword, funnel_step, ...)
    target_kind: TargetKind
    confidence: float      # 0..1
    source_ids: list[str]  # content_ids/keyword_ids/etc. backing this rec
    first_seen_period: str # ISO date string, the period the rec first appeared
    applied_at: Optional[str] = None
    rationale: Optional[str] = None  # LLM-generated explanation

    def __post_init__(self) -> None:
        # Coerce string inputs to enums for callers that pass strings
        if isinstance(self.pillar, str):
            self.pillar = Pillar(self.pillar)
        if isinstance(self.target_kind, str):
            self.target_kind = TargetKind(self.target_kind)


def persist_recommendation(
    db_path: Path, report_id: str, rec: Recommendation
) -> None:
    """Insert a Recommendation into `analytics_recommendations`.

    Validates `(pillar, target_kind)` before INSERT — accidental cross-pillar
    target_kinds are caught here, not at calibration time.
    """
    validate_target_kind_for_pillar(rec.pillar, rec.target_kind)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analytics_recommendations
                (report_id, action, target, source_ids_json, confidence,
                 first_seen_period, applied_at, pillar, target_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                rec.action,
                rec.target,
                json.dumps(rec.source_ids),
                rec.confidence,
                rec.first_seen_period,
                rec.applied_at,
                rec.pillar.value,
                rec.target_kind.value,
            ),
        )
        conn.commit()


def find_open_by_target(db_path: Path, pillar: Pillar) -> list[Recommendation]:
    """Return all unapplied recommendations for a pillar, newest-first."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NULL
            ORDER BY first_seen_period DESC
            """,
            (pillar.value,),
        )
        return [
            Recommendation(
                pillar=Pillar(row[0]),
                action=row[1],
                target=row[2],
                target_kind=TargetKind(row[3]),
                confidence=row[4],
                source_ids=json.loads(row[5] or "[]"),
                first_seen_period=row[6],
                applied_at=row[7],
            )
            for row in cur.fetchall()
        ]


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
    """Return open recommendations whose `first_seen_period` is N+ periods old.

    `period` here is calendar weeks; `stale_after_periods=2` means
    "first_seen ≥ 14 days before current_period" → stale.
    """
    cutoff = date.fromisoformat(current_period) - timedelta(weeks=stale_after_periods)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NULL
              AND first_seen_period <= ?
            ORDER BY first_seen_period ASC
            """,
            (pillar.value, cutoff.isoformat()),
        )
        return [
            Recommendation(
                pillar=Pillar(row[0]),
                action=row[1],
                target=row[2],
                target_kind=TargetKind(row[3]),
                confidence=row[4],
                source_ids=json.loads(row[5] or "[]"),
                first_seen_period=row[6],
                applied_at=row[7],
            )
            for row in cur.fetchall()
        ]
```

Update `src/devrel_swarm/core/growth/__init__.py` to export the new symbols:

```python
"""Shared helpers for the Growth pipeline (Selene/Vega/Cyra + Argus)."""

from devrel_swarm.core.growth.recommendations import (
    Recommendation,
    find_open_by_target,
    find_stale,
    mark_applied,
    persist_recommendation,
)
from devrel_swarm.core.growth.target_kinds import (
    Pillar,
    TargetKind,
    validate_target_kind_for_pillar,
)

__all__ = [
    "Pillar",
    "TargetKind",
    "Recommendation",
    "persist_recommendation",
    "find_open_by_target",
    "mark_applied",
    "find_stale",
    "validate_target_kind_for_pillar",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/growth/test_recommendations.py -v --no-cov
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/growth/recommendations.py src/devrel_swarm/core/growth/__init__.py tests/core/growth/test_recommendations.py
git commit -m "feat(growth): pillar-agnostic Recommendation persistence + lifecycle"
```

---

## Task 5: Calibration helper

**Files:**
- Modify: `src/devrel_swarm/core/growth/recommendations.py`
- Modify: `src/devrel_swarm/core/growth/__init__.py`
- Create: `tests/core/growth/test_calibration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/growth/test_calibration.py`:

```python
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
def report_id(db: Path) -> str:
    rid = "test-report"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (id, period_end, generated_at, body_json) "
            "VALUES (?, ?, datetime('now'), '{}')",
            (rid, "2026-04-01"),
        )
        conn.commit()
    return rid


class TestCalibrate:
    def test_per_action_hit_rate(self, db: Path, report_id: str):
        """Two `double_down` recs, one applied + outcome=improved → hit rate 1.0."""
        rec1 = Recommendation(
            pillar=Pillar.SEO, action="double_down", target="/a", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-01",
            applied_at="2026-03-08T00:00:00",
        )
        rec2 = Recommendation(
            pillar=Pillar.SEO, action="double_down", target="/b", target_kind=TargetKind.URL,
            confidence=0.8, source_ids=[], first_seen_period="2026-03-01",
            applied_at=None,  # never applied → not in calibration
        )
        persist_recommendation(db, report_id, rec1)
        persist_recommendation(db, report_id, rec2)

        # outcome scorer always returns 'improved' for this test
        def fake_outcome_scorer(rec: Recommendation) -> str:
            return "improved"

        result = calibrate(db, Pillar.SEO, outcome_scorer=fake_outcome_scorer)
        assert result["double_down"]["applied_count"] == 1
        assert result["double_down"]["hit_rate"] == 1.0

    def test_pillar_filter(self, db: Path, report_id: str):
        """Calibration filters by pillar — SEO recs don't show up in CRO calibration."""
        seo_rec = Recommendation(
            pillar=Pillar.SEO, action="double_down", target="/a", target_kind=TargetKind.URL,
            confidence=0.9, source_ids=[], first_seen_period="2026-03-01",
            applied_at="2026-03-08T00:00:00",
        )
        persist_recommendation(db, report_id, seo_rec)

        cro_calibration = calibrate(db, Pillar.CRO, outcome_scorer=lambda r: "improved")
        assert cro_calibration == {}

    def test_lift_vs_coin_flip(self, db: Path, report_id: str):
        """3 of 4 applied recs improved → hit_rate=0.75; lift_vs_coinflip = 0.75 - 0.5 = 0.25."""
        for i, outcome in enumerate(["improved", "improved", "improved", "regressed"]):
            rec = Recommendation(
                pillar=Pillar.SEO, action="double_down", target=f"/p{i}", target_kind=TargetKind.URL,
                confidence=0.8, source_ids=[], first_seen_period="2026-03-01",
                applied_at=f"2026-03-{i+8:02d}T00:00:00",
            )
            persist_recommendation(db, report_id, rec)

        outcomes_iter = iter(["improved", "improved", "improved", "regressed"])
        result = calibrate(
            db, Pillar.SEO, outcome_scorer=lambda r: next(outcomes_iter)
        )
        assert result["double_down"]["applied_count"] == 4
        assert result["double_down"]["hit_rate"] == 0.75
        assert result["double_down"]["lift_vs_coinflip"] == pytest.approx(0.25)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/growth/test_calibration.py -v --no-cov
```

Expected: ImportError on `calibrate` — function doesn't exist.

- [ ] **Step 3: Add `calibrate` to `recommendations.py`**

Append to `src/devrel_swarm/core/growth/recommendations.py`:

```python
from collections import defaultdict
from typing import Callable


def calibrate(
    db_path: Path,
    pillar: Pillar,
    *,
    outcome_scorer: Callable[[Recommendation], str],
) -> dict[str, dict[str, float | int]]:
    """Per-action hit-rate calibration for one pillar's applied recommendations.

    `outcome_scorer(rec)` returns one of {'improved', 'unchanged', 'regressed'}.
    Each pillar implements its own scorer based on subsequent fact-table rows
    (e.g. SEO checks if keyword position improved; CRO checks if conversion
    rate rose). This helper just aggregates.

    Returns: {action: {applied_count, hit_rate, lift_vs_coinflip,
                       avg_confidence, high_conf_hit_rate}}
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT pillar, action, target, target_kind, confidence,
                   source_ids_json, first_seen_period, applied_at
            FROM analytics_recommendations
            WHERE pillar = ? AND applied_at IS NOT NULL
            """,
            (pillar.value,),
        )
        rows = cur.fetchall()

    by_action: dict[str, list[tuple[Recommendation, str]]] = defaultdict(list)
    for row in rows:
        rec = Recommendation(
            pillar=Pillar(row[0]),
            action=row[1],
            target=row[2],
            target_kind=TargetKind(row[3]),
            confidence=row[4],
            source_ids=json.loads(row[5] or "[]"),
            first_seen_period=row[6],
            applied_at=row[7],
        )
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
```

Update `src/devrel_swarm/core/growth/__init__.py` to add `calibrate` to the import + `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/growth/test_calibration.py -v --no-cov
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/growth/recommendations.py src/devrel_swarm/core/growth/__init__.py tests/core/growth/test_calibration.py
git commit -m "feat(growth): per-pillar calibration helper with hit-rate + lift"
```

---

## Task 6: Refactor Argus to use shared `growth` module

**Files:**
- Modify: `src/devrel_swarm/core/argus.py`
- Modify: `tests/test_argus.py` (any tests touching `_persist`)

- [ ] **Step 1: Find Argus's existing `_persist` and `calibrate_recommendations`**

```bash
grep -n "_persist\|calibrate_recommendations\|analytics_recommendations" src/devrel_swarm/core/argus.py | head -20
```

- [ ] **Step 2: Replace inline INSERT with `growth.persist_recommendation`**

In `src/devrel_swarm/core/argus.py`, replace any inline INSERT into `analytics_recommendations` with:

```python
from devrel_swarm.core.growth import (
    Pillar,
    Recommendation as GrowthRecommendation,
    TargetKind,
    persist_recommendation,
)

# Inside the existing _persist method, replace the manual INSERT loop with:
for rec in report.recommendations:
    growth_rec = GrowthRecommendation(
        pillar=Pillar.ARGUS,
        action=rec.action,
        target=rec.target,
        target_kind=TargetKind.CONTENT_ID,
        confidence=rec.confidence,
        source_ids=rec.source_ids,
        first_seen_period=report.period_end,
        rationale=getattr(rec, "rationale", None),
    )
    persist_recommendation(self._db_path, report.id, growth_rec)
```

- [ ] **Step 3: Run the existing Argus test suite**

```bash
pytest tests/test_argus.py -v --no-cov
```

Expected: all PASS (Argus's own Recommendation dataclass still exists; it's just the persistence path that delegates). If any tests fail because they assert on raw SQL or persistence side effects, update them to use `find_open_by_target(db, Pillar.ARGUS)`.

- [ ] **Step 4: Run the full test suite to confirm no regression**

```bash
pytest tests/ -q --no-header
```

Expected: full suite still 815+ passed (the +tests-from-this-wave) / 21 xfailed.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/core/argus.py tests/test_argus.py
git commit -m "refactor(argus): delegate persistence to growth.persist_recommendation"
```

---

## Task 7: Rename `cli/analytics.py` to `cli/argus.py`

**Files:**
- Create: `src/devrel_swarm/cli/argus.py` (copy of analytics.py with module-level rename)
- Modify: `src/devrel_swarm/cli/analytics.py` (becomes deprecation alias)
- Modify: `src/devrel_swarm/cli/__init__.py`
- Test: `tests/cli/test_analytics_alias.py`

- [ ] **Step 1: Write the failing alias test**

Create `tests/cli/test_analytics_alias.py`:

```python
"""Verify `devrel analytics ...` still works as an alias for `devrel argus ...`."""

import warnings

from typer.testing import CliRunner

from devrel_swarm.cli import app


def test_analytics_subcommand_runs_with_deprecation_warning():
    runner = CliRunner()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = runner.invoke(app, ["analytics", "--help"])
    assert result.exit_code == 0
    # Help output mentions argus aliasing
    assert "argus" in result.output.lower() or "deprecated" in result.output.lower()


def test_argus_subcommand_runs_directly():
    runner = CliRunner()
    result = runner.invoke(app, ["argus", "--help"])
    assert result.exit_code == 0
    assert "report" in result.output.lower()
```

- [ ] **Step 2: Run test to confirm fail**

```bash
pytest tests/cli/test_analytics_alias.py -v --no-cov
```

Expected: `argus` command not registered → fail.

- [ ] **Step 3: Copy analytics.py → argus.py + add alias shim**

```bash
cp src/devrel_swarm/cli/analytics.py src/devrel_swarm/cli/argus.py
```

Edit `src/devrel_swarm/cli/argus.py`: rename the Typer app variable from `analytics_app` to `argus_app` (search/replace all in the file).

Replace `src/devrel_swarm/cli/analytics.py` with:

```python
"""Deprecated alias — use `devrel argus ...`. Retained until v1.0 for backward compat."""

import warnings

import typer

from devrel_swarm.cli.argus import argus_app

analytics_app = typer.Typer(
    name="analytics",
    help="DEPRECATED — use `devrel argus` instead. Forwarding to argus...",
    invoke_without_command=False,
)


@analytics_app.callback()
def _deprecation_notice() -> None:
    warnings.warn(
        "`devrel analytics ...` is deprecated; use `devrel argus ...` instead. "
        "The alias will be removed in v1.0.",
        DeprecationWarning,
        stacklevel=2,
    )


# Forward all subcommands from argus_app
for cmd in argus_app.registered_commands:
    analytics_app.registered_commands.append(cmd)
```

Update `src/devrel_swarm/cli/__init__.py` to register both:

```python
# In the imports section:
from devrel_swarm.cli.analytics import analytics_app
from devrel_swarm.cli.argus import argus_app

# In the body that registers Typer subgroups:
app.add_typer(argus_app, name="argus")
app.add_typer(analytics_app, name="analytics")  # deprecated alias
```

- [ ] **Step 4: Run alias tests + full suite**

```bash
pytest tests/cli/test_analytics_alias.py -v --no-cov
pytest tests/ -q --no-header
```

Expected: alias tests PASS; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/cli/argus.py src/devrel_swarm/cli/analytics.py src/devrel_swarm/cli/__init__.py tests/cli/test_analytics_alias.py
git commit -m "refactor(cli): rename analytics → argus with deprecation alias"
```

---

## Task 8: `cli/growth.py` umbrella with `summary` + `diff` placeholders

**Files:**
- Create: `src/devrel_swarm/cli/growth.py`
- Modify: `src/devrel_swarm/cli/__init__.py`
- Test: `tests/cli/test_growth_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_growth_command.py`:

```python
"""Smoke tests for the cross-pillar `devrel growth` umbrella."""

from typer.testing import CliRunner

from devrel_swarm.cli import app


class TestGrowthUmbrella:
    def test_growth_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "--help"])
        assert result.exit_code == 0
        assert "summary" in result.output.lower()
        assert "diff" in result.output.lower()

    def test_growth_summary_runs(self, tmp_path, monkeypatch):
        """Placeholder summary verb should exit 0 even with no data."""
        monkeypatch.chdir(tmp_path)
        # Create a minimal .devrel/ so the command finds project state
        (tmp_path / ".devrel").mkdir()
        (tmp_path / ".devrel" / "config.toml").write_text(
            'product_name = "Test"\nproduct_url = "https://example.com"\n'
        )
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "summary"])
        assert result.exit_code == 0

    def test_growth_diff_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".devrel").mkdir()
        (tmp_path / ".devrel" / "config.toml").write_text(
            'product_name = "Test"\nproduct_url = "https://example.com"\n'
        )
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "diff", "2026-04-01", "2026-04-08"])
        assert result.exit_code == 0
```

- [ ] **Step 2: Run test to confirm fail**

```bash
pytest tests/cli/test_growth_command.py -v --no-cov
```

Expected: `growth` command not registered → fail.

- [ ] **Step 3: Create the umbrella module**

Create `src/devrel_swarm/cli/growth.py`:

```python
"""Cross-pillar `devrel growth` umbrella.

`summary` rolls up the latest report from each pillar (argus + cyra +
vega + selene) into a single Markdown table. `diff` shows pillar-level
movement between two periods. Pillar-specific verbs live in
`cli/{seo,geo,cro,argus}.py`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.growth.target_kinds import Pillar

growth_app = typer.Typer(
    name="growth",
    help="Cross-pillar Growth dashboard. Pillar-specific verbs live in `seo`, `geo`, `cro`, `argus`.",
    no_args_is_help=True,
)

_console = Console()


@growth_app.command("summary")
def summary(
    period: str = typer.Option("", "--period", help="ISO period (default: latest)"),
) -> None:
    """One-line-per-pillar status of the most recent report."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet — run `devrel run` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title="Growth — pillar summary")
    table.add_column("Pillar", style="cyan")
    table.add_column("Open recs")
    table.add_column("Latest period")

    with sqlite3.connect(db_path) as conn:
        for pillar in Pillar:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS open_recs, MAX(first_seen_period) AS latest
                FROM analytics_recommendations
                WHERE pillar = ? AND applied_at IS NULL
                """,
                (pillar.value,),
            )
            row = cur.fetchone()
            open_recs = row[0] or 0
            latest = row[1] or "-"
            table.add_row(pillar.value, str(open_recs), latest)

    _console.print(table)


@growth_app.command("diff")
def diff(
    period_a: str = typer.Argument(..., help="Earlier ISO period"),
    period_b: str = typer.Argument(..., help="Later ISO period"),
) -> None:
    """Per-pillar count of new/closed recommendations between two periods."""
    paths = find_paths_or_exit()
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet — run `devrel run` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"Growth diff — {period_a} → {period_b}")
    table.add_column("Pillar", style="cyan")
    table.add_column("New", style="green")
    table.add_column("Closed", style="dim")

    with sqlite3.connect(db_path) as conn:
        for pillar in Pillar:
            cur = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN first_seen_period > ? AND first_seen_period <= ? THEN 1 ELSE 0 END) AS new_count,
                    SUM(CASE WHEN applied_at IS NOT NULL
                              AND date(applied_at) > ? AND date(applied_at) <= ?
                          THEN 1 ELSE 0 END) AS closed_count
                FROM analytics_recommendations
                WHERE pillar = ?
                """,
                (period_a, period_b, period_a, period_b, pillar.value),
            )
            row = cur.fetchone()
            table.add_row(pillar.value, str(row[0] or 0), str(row[1] or 0))

    _console.print(table)
```

Update `src/devrel_swarm/cli/__init__.py`:

```python
from devrel_swarm.cli.growth import growth_app
# ...
app.add_typer(growth_app, name="growth")
```

- [ ] **Step 4: Run the tests**

```bash
pytest tests/cli/test_growth_command.py -v --no-cov
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_swarm/cli/growth.py src/devrel_swarm/cli/__init__.py tests/cli/test_growth_command.py
git commit -m "feat(cli): cross-pillar `devrel growth {summary|diff}` umbrella"
```

---

## Task 9: Add `[seo]`, `[geo-google]`, `[growth]` extras to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read the current `[project.optional-dependencies]` block**

```bash
sed -n '/optional-dependencies/,/^\[project.urls\]/p' pyproject.toml
```

- [ ] **Step 2: Add the new extras**

In `pyproject.toml`, replace the `[project.optional-dependencies]` block with:

```toml
[project.optional-dependencies]
# Video tutorial generation (Vox agent). Adds ~150MB Playwright browsers
# (after `playwright install`), pyobjc-core on macOS, and pulls openai for
# TTS narration. Install with `pip install 'devrel-swarm[video]'`.
video = [
    "openai>=1.50.0",
    "playwright>=1.49.0",
    "pyautogui>=0.9.54",
]

# SEO auditor (Selene). Pulls Google API client + OAuth flow + HTML parser.
# Install with `pip install 'devrel-swarm[seo]'` (or `[growth]` for full pipeline).
seo = [
    "google-api-python-client>=2.150.0",
    "google-auth-oauthlib>=1.2.0",
    "google-auth-httplib2>=0.2.0",
    "beautifulsoup4>=4.12.0",
]

# Optional 5th GEO engine (Google AI Overviews via SerpAPI). Off by default;
# enable in config.toml `[geo].include_google_ai_overviews = true`.
geo-google = [
    "google-search-results>=2.4.2",
]

# Convenience: full Growth pipeline (Selene + Vega + Cyra). GEO + CRO have
# zero new deps; their AI clients reuse existing openai/anthropic/httpx.
growth = [
    "devrel-swarm[seo]",
]

dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "respx>=0.20.2",
    "ruff>=0.1.0",
    "mypy>=1.5.0",
    "build>=1.0.0",
    "twine>=5.0.0",
    "devrel-swarm[video,growth]",
]
```

- [ ] **Step 3: Reinstall in editable mode + verify build still clean**

```bash
cd ~/devrel-swarm && source .venv/bin/activate
pip install -e ".[dev]" --quiet
ruff check . && ruff format --check . | tail -1
rm -rf dist/ build/ && python -m build 2>&1 | tail -2
python -m twine check dist/* 2>&1 | tail -2
```

Expected: ruff clean, build clean, twine PASSED.

- [ ] **Step 4: Verify fresh-venv install of `[growth]` extra pulls the SEO deps**

```bash
rm -rf /tmp/devrel-growth-test && python3.13 -m venv /tmp/devrel-growth-test
source /tmp/devrel-growth-test/bin/activate
pip install --quiet "$HOME/devrel-swarm/dist/devrel_swarm-0.2.4-py3-none-any.whl[growth]"
pip list 2>/dev/null | grep -iE "google-api|google-auth|beautifulsoup"
```

Expected: 3+ google-* packages and beautifulsoup4 installed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add [seo], [geo-google], [growth] optional extras"
```

---

## Task 10: Manual setup — Google Cloud + OAuth verification submission

**Files:**
- Create: `docs/setup-google-oauth.md`

This task is half-document, half-manual-action. The OAuth verification clock is the long pole for Selene (Wave 3); kicking off the submission in Wave 0 means the verification queue is moving in the background while Cyra and Vega get built.

- [ ] **Step 1: Write the walkthrough doc**

Create `docs/setup-google-oauth.md`:

```markdown
# Setting up the shared "devrel-swarm" Google OAuth project

`devrel seo connect-gsc` runs a standard OAuth 2.0 installed-app flow against a
GCP project owned by Daria. Users never set their own client_id/secret —
they consent against the shared "devrel-swarm" app the first time they
connect, and refresh tokens are stored locally at
`.devrel/credentials/gsc.json`.

This doc is the one-time setup Daria runs to provision the shared project +
submit it for Google verification (so end users don't see the "unverified app"
warning indefinitely).

## 1. Create the GCP project

1. Sign into https://console.cloud.google.com with the account that should
   own the OAuth client (recommend a dedicated devrel-swarm@ Google Workspace
   account separate from personal Gmail).
2. Click the project selector (top bar) → "New Project".
3. Project name: `devrel-swarm`, no organisation, click Create.
4. Wait ~30 seconds for provisioning, then select the new project.

## 2. Enable the Search Console API

1. Navigation menu → APIs & Services → Library.
2. Search "Search Console API" → Enable.

## 3. Configure the OAuth consent screen

1. Navigation menu → APIs & Services → OAuth consent screen.
2. User type: **External**. Click Create.
3. App information:
   - App name: `devrel-swarm`
   - User support email: `dovzhikova@gmail.com` (or the Workspace email)
   - App logo: 120x120 png at <https://gtm-labs.co/devrel-swarm/logo.png> (or upload local)
   - App domain: `https://gtm-labs.co/devrel-swarm`
   - Authorized domains: `gtm-labs.co`
   - Developer contact: `dovzhikova@gmail.com`
4. Scopes: Add the `https://www.googleapis.com/auth/webmasters.readonly` scope (read-only Search Console).
5. Test users: leave blank for now (we'll switch from Testing → In production after verification).
6. Save.

## 4. Create the OAuth client

1. Navigation menu → APIs & Services → Credentials → "+ CREATE CREDENTIALS" → OAuth client ID.
2. Application type: **Desktop app**.
3. Name: `devrel-swarm CLI`.
4. Save. Click the download (⤓) icon next to the new credential to grab the
   JSON. The relevant fields are `client_id` and `client_secret`.

## 5. Embed the client_id/secret in the package

The OAuth installed-app flow safely embeds `client_id` + `client_secret` —
they are not "secrets" in the cryptographic sense; they identify the app to
Google. Anyone could intercept them by inspecting the request, but the actual
auth happens against the user's Google account, not the client_secret. (Google
docs: https://developers.google.com/identity/protocols/oauth2/native-app)

Edit `src/devrel_swarm/core/oauth_constants.py` (created in Wave 3, Task 1):

```python
GSC_OAUTH_CLIENT_ID = "<paste here>.apps.googleusercontent.com"
GSC_OAUTH_CLIENT_SECRET = "<paste here>"
```

For self-hosting maintainers who want to run their own GCP project, the
values can be overridden via env vars `GSC_OAUTH_CLIENT_ID` and
`GSC_OAUTH_CLIENT_SECRET` (see `tools/gsc_client.py` in Wave 3).

## 6. Submit for verification

1. Navigation menu → APIs & Services → OAuth consent screen.
2. "Publishing status" section → click "PUBLISH APP".
3. Confirm.
4. Click "Prepare for verification".
5. Fill in:
   - Justification for the `webmasters.readonly` scope: "Read-only access to Search Console data is required to surface keyword performance and crawl issues to the user. The user explicitly opts in via the `devrel seo connect-gsc` command. Data is not shared, sold, or transmitted off the user's device."
   - Demo video URL: link to a 1-minute screencast of `devrel seo connect-gsc` running. Record this when Wave 3 lands.
6. Submit. Google reviews queue is typically 4-6 weeks.

## 7. While verification is pending

The OAuth flow still works with the consent screen showing
"Google hasn't verified this app". Users can proceed via "Advanced →
Continue to devrel-swarm". This is acceptable for the first 100 users
(Google's "Testing" mode quota). Document this in
`docs/seo-setup.md` (Wave 4) so users aren't surprised.

## 8. After verification

Google will email approval. The unverified-app warning disappears for all
users automatically; no code change required.
```

- [ ] **Step 2: Walk through the doc end-to-end on console.cloud.google.com**

(Manual — Daria runs through steps 1-6 in browser. No code task. Outcome: a
GCP project exists, the OAuth consent screen is configured, the desktop OAuth
client is created, the verification application is submitted.)

- [ ] **Step 3: Save the OAuth client_id/secret in 1Password**

(Manual — Daria stashes the credentials JSON in her password manager so
Wave 3 Task 1 can retrieve them. The values land in `core/oauth_constants.py`
during Wave 3.)

- [ ] **Step 4: Commit the doc**

```bash
git add docs/setup-google-oauth.md
git commit -m "docs: walkthrough for the shared devrel-swarm GCP OAuth project"
```

- [ ] **Step 5: (Optional) Push and request the Wave 0 review**

```bash
git push origin main
```

---

## Wave 0 closeout checklist

After Tasks 1-10:

- [ ] `pytest tests/ -q --no-header` shows 815 + ~25 new tests = ~840 passed / 21 xfailed (or higher)
- [ ] `ruff check .` and `ruff format --check .` both clean
- [ ] `python -m build && python -m twine check dist/*` clean
- [ ] `pip install -e ".[growth]"` in a fresh py3.13 venv pulls google-api-python-client + beautifulsoup4
- [ ] `devrel growth summary` runs (returns "No state.db yet" placeholder)
- [ ] `devrel argus report --help` works
- [ ] `devrel analytics report --help` works AND emits a DeprecationWarning
- [ ] `docs/setup-google-oauth.md` published; Google verification submitted
- [ ] OAuth client_id/secret stashed in 1Password for Wave 3

When all checked: Wave 0 complete. Move to Wave 1 plan (`growth-wave1-cyra-cro.md`).
