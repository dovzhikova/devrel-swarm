"""SQLite persistence for a single devrel-swarm instance.

Schema is flat + denormalized. Each instance has its own DB file under /data.
v0: no migration tooling — `init()` is idempotent CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    completed_at TEXT,
    cost_cents REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_checkpoints (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cost_events (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS cost_events_month_idx
    ON cost_events (substr(created_at, 1, 7));

CREATE TABLE IF NOT EXISTS deliverables (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    quality_score REAL,
    voice_score REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    raw_payload_json TEXT NOT NULL,
    sentiment TEXT,
    priority TEXT,
    theme_tags_json TEXT NOT NULL DEFAULT '[]',
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quality_events (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class InstanceStorage:
    """Async SQLite wrapper for a single instance's persistence."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(_DDL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("InstanceStorage not initialized; call init() first")
        return self._conn

    # -- Jobs ---------------------------------------------------------------
    async def create_job(self, kind: str) -> str:
        conn = self._require()
        job_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO jobs (id, kind, status, started_at) "
            "VALUES (?, ?, 'running', datetime('now'))",
            (job_id, kind),
        )
        await conn.commit()
        return job_id

    async def update_job(
        self,
        job_id: str,
        status: str | None = None,
        error: str | None = None,
        cost_cents: float | None = None,
    ) -> None:
        conn = self._require()
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
            if status in ("completed", "failed"):
                fields.append("completed_at = datetime('now')")
        if error is not None:
            fields.append("error_message = ?")
            params.append(error)
        if cost_cents is not None:
            fields.append("cost_cents = ?")
            params.append(cost_cents)
        if not fields:
            return
        params.append(job_id)
        await conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", tuple(params)
        )
        await conn.commit()

    async def list_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- Checkpoints --------------------------------------------------------
    async def save_checkpoint(
        self, job_id: str, stage: str, payload: dict[str, Any]
    ) -> None:
        conn = self._require()
        await conn.execute(
            "INSERT INTO job_checkpoints (id, job_id, stage, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), job_id, stage, json.dumps(payload)),
        )
        await conn.commit()

    async def latest_checkpoint(self, job_id: str) -> dict[str, Any] | None:
        conn = self._require()
        # ORDER BY rowid as tiebreaker: datetime('now') has 1s resolution,
        # so two checkpoints saved in the same second would otherwise be
        # non-deterministically ordered.
        cursor = await conn.execute(
            "SELECT stage, payload_json, created_at FROM job_checkpoints "
            "WHERE job_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "stage": row["stage"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    # -- Cost events --------------------------------------------------------
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
    ) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT INTO cost_events (
                id, job_id, agent, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                cost_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), job_id, agent, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                cost_cents,
            ),
        )
        await conn.commit()

    async def monthly_spend_cents(self) -> float:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_cents), 0) AS total FROM cost_events "
            "WHERE substr(created_at, 1, 7) = strftime('%Y-%m', 'now')"
        )
        row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    # -- Deliverables -------------------------------------------------------
    async def insert_deliverable(
        self,
        job_id: str | None,
        kind: str,
        title: str,
        body_md: str | None = None,
        quality_score: float | None = None,
        voice_score: float | None = None,
    ) -> str:
        conn = self._require()
        d_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO deliverables (
                id, job_id, kind, title, body_md, quality_score, voice_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (d_id, job_id, kind, title, body_md, quality_score, voice_score),
        )
        await conn.commit()
        return d_id

    async def list_deliverables(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM deliverables ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_deliverable(self, d_id: str) -> dict[str, Any] | None:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM deliverables WHERE id = ?", (d_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
