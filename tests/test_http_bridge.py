"""Tests for the HTTP bridge FastAPI app."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "instance.db")
        monkeypatch.setenv("INSTANCE_DB_PATH", db_path)
        monkeypatch.setenv("INSTANCE_API_TOKEN", "test-token-123")
        monkeypatch.setenv("OPTIMIZE_DIR", str(Path(d) / "optimize"))
        (Path(d) / "optimize" / "kai").mkdir(parents=True)
        (Path(d) / "optimize" / "kai" / "system_prompt.txt").write_text("seed prompt")

        # Import after env vars are set so module-level env reads pick them up.
        # Reset any module-scoped storage singleton between tests.
        import tools.http_bridge as hb
        hb._storage = None  # defensive reset

        app = hb.create_app()
        with TestClient(app) as c:
            yield c

        # Clean up the module-scoped storage so the next test gets a fresh one.
        hb._storage = None


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_protected_endpoints_require_token(client):
    res = client.get("/api/deliverables")
    assert res.status_code == 401


def test_list_jobs_empty(client):
    res = client.get(
        "/api/jobs", headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"jobs": []}


def test_list_deliverables_empty(client):
    res = client.get(
        "/api/deliverables", headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"deliverables": []}


def test_read_prompt(client):
    res = client.get(
        "/api/prompts/kai",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"agent": "kai", "prompt": "seed prompt"}


def test_write_prompt(client):
    res = client.put(
        "/api/prompts/kai",
        json={"prompt": "new prompt"},
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200

    res2 = client.get(
        "/api/prompts/kai",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res2.json()["prompt"] == "new prompt"


def test_read_prompt_missing(client):
    res = client.get(
        "/api/prompts/nonexistent_agent",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 404


def test_month_cost_empty(client):
    res = client.get(
        "/api/cost/month",
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    assert res.json() == {"cents": 0.0}


def test_invalid_token_rejected(client):
    res = client.get(
        "/api/jobs",
        headers={"authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 401


def test_trigger_run_returns_job_id(client):
    res = client.post(
        "/api/run",
        json={"kind": "weekly_cycle"},
        headers={"authorization": "Bearer test-token-123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "job_id" in body
    assert body["status"] == "queued"

    # The created job should now appear in /api/jobs
    jobs = client.get(
        "/api/jobs", headers={"authorization": "Bearer test-token-123"},
    ).json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == body["job_id"]
    assert jobs[0]["kind"] == "weekly_cycle"
