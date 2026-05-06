"""POST /v1/recommendations snapshot mode (empty body → Postgres)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent.app.main import app
from agent.app.state import RecommendationRequest, recommendation_request_has_inline_payload


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_inline_payload_detection():
    assert recommendation_request_has_inline_payload(RecommendationRequest(signals={"cpu": 0.1})) is True
    assert recommendation_request_has_inline_payload(RecommendationRequest()) is False
    topo_only = RecommendationRequest(topology={"services": ["x"], "edges": []})
    assert recommendation_request_has_inline_payload(topo_only) is True


def test_snapshot_mode_uses_fetch(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    snap = uuid4()

    def fake_fetch(settings, rid):
        assert rid is None
        return (
            {"cpu_utilization": 0.5, "memory_utilization": 0.4},
            {"services": ["api"], "edges": [], "critical_stores": [], "critical_queues": []},
            snap,
        )

    monkeypatch.setattr("agent.app.main.fetch_snapshot_raw", fake_fetch)
    r = client.post("/v1/recommendations", json={})
    assert r.status_code == 200
    data = r.json()
    assert "recommendations" in data
    assert isinstance(data.get("explanation_report"), str)


def test_snapshot_mode_missing_dsn(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def boom(settings, rid):
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is not set")

    monkeypatch.setattr("agent.app.main.fetch_snapshot_raw", boom)
    r = client.post("/v1/recommendations", json={})
    assert r.status_code == 503


def test_snapshot_mode_no_rows(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def _no_snapshot(settings, rid):
        raise LookupError("no_snapshot")

    monkeypatch.setattr("agent.app.main.fetch_snapshot_raw", _no_snapshot)
    r = client.post("/v1/recommendations", json={})
    assert r.status_code == 503


def test_snapshot_mode_unknown_run(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def _not_found(settings, rid):
        raise LookupError("run_not_found")

    monkeypatch.setattr("agent.app.main.fetch_snapshot_raw", _not_found)
    rid = uuid4()
    r = client.post(f"/v1/recommendations?run_id={rid}")
    assert r.status_code == 404
