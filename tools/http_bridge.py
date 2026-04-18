"""HTTP bridge around the devrel-swarm instance state for the central app.

Each customer Fly Machine runs this as `uvicorn tools.http_bridge:app --port 8787`.
The central Next.js app authenticates with a shared bearer token
(INSTANCE_API_TOKEN) and calls these endpoints.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from tools.storage import InstanceStorage

logger = logging.getLogger(__name__)

# Module-scoped storage singleton. Tests reset this between invocations.
_storage: InstanceStorage | None = None


def _db_path() -> str:
    return os.environ.get("INSTANCE_DB_PATH", "/data/instance.db")


def _optimize_dir() -> Path:
    return Path(os.environ.get("OPTIMIZE_DIR", "optimize"))


def _expected_token() -> str:
    tok = os.environ.get("INSTANCE_API_TOKEN", "")
    if not tok:
        logger.warning(
            "INSTANCE_API_TOKEN not set — bridge is unauthenticated"
        )
    return tok


async def _get_storage() -> InstanceStorage:
    global _storage
    if _storage is None:
        _storage = InstanceStorage(db_path=_db_path())
        await _storage.init()
    return _storage


async def require_bearer(request: Request) -> None:
    expected = _expected_token()
    if not expected:
        return  # unauthenticated mode for tests / local dev without env
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    if header[len("Bearer "):].strip() != expected:
        raise HTTPException(status_code=401, detail="invalid token")


class PromptBody(BaseModel):
    prompt: str


class RunTriggerBody(BaseModel):
    kind: str = "weekly_cycle"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _get_storage()
        yield
        global _storage
        if _storage is not None:
            await _storage.close()
            _storage = None

    app = FastAPI(title="devrel-swarm instance bridge", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/jobs", dependencies=[Depends(require_bearer)])
    async def list_jobs(limit: int = 20) -> dict[str, Any]:
        storage = await _get_storage()
        return {"jobs": await storage.list_jobs(limit=limit)}

    @app.get("/api/deliverables", dependencies=[Depends(require_bearer)])
    async def list_deliverables(limit: int = 50) -> dict[str, Any]:
        storage = await _get_storage()
        return {"deliverables": await storage.list_deliverables(limit=limit)}

    @app.get(
        "/api/deliverables/{d_id}",
        dependencies=[Depends(require_bearer)],
    )
    async def get_deliverable(d_id: str) -> dict[str, Any]:
        storage = await _get_storage()
        row = await storage.get_deliverable(d_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return row

    @app.get("/api/cost/month", dependencies=[Depends(require_bearer)])
    async def month_cost() -> dict[str, float]:
        storage = await _get_storage()
        return {"cents": await storage.monthly_spend_cents()}

    @app.get(
        "/api/prompts/{agent}", dependencies=[Depends(require_bearer)],
    )
    async def read_prompt(agent: str) -> dict[str, str]:
        fp = _optimize_dir() / agent / "system_prompt.txt"
        if not fp.exists():
            raise HTTPException(status_code=404, detail="prompt not found")
        return {"agent": agent, "prompt": fp.read_text()}

    @app.put(
        "/api/prompts/{agent}", dependencies=[Depends(require_bearer)],
    )
    async def write_prompt(agent: str, body: PromptBody) -> dict[str, str]:
        fp = _optimize_dir() / agent / "system_prompt.txt"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body.prompt)
        return {"agent": agent, "status": "written"}

    @app.post("/api/run", dependencies=[Depends(require_bearer)])
    async def trigger_run(body: RunTriggerBody) -> dict[str, str]:
        """Stub: creates a job row and returns its id. A.5 wires the dispatcher."""
        storage = await _get_storage()
        job_id = await storage.create_job(kind=body.kind)
        return {"job_id": job_id, "status": "queued"}

    return app


# Module-level app instance for `uvicorn tools.http_bridge:app`
app = create_app()
