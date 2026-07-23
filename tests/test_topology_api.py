from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from agent.app.api.topology import _filter_graph_namespaces
from agent.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_topology_endpoint_returns_latest_snapshot_graph(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    snap = uuid4()

    def fake_fetch(settings, rid):
        assert rid is None
        return (
            snap,
            {
                "nodes": [
                    {
                        "id": "k8s:default:workload:api",
                        "name": "api",
                        "kind": "workload",
                        "platform": "kubernetes",
                        "status": "healthy",
                        "severity": "none",
                        "data_sources": ["kubernetes"],
                    }
                ],
                "edges": [],
                "meta": {"run_id": str(snap), "node_count": 1, "edge_count": 0, "topology_confidence": "low"},
            },
            {"topology_edges_inferred": 0, "topology_confidence": "low"},
        )

    monkeypatch.setattr("agent.app.api.topology.fetch_topology_graph", fake_fetch)
    r = client.get("/v1/topology")

    assert r.status_code == 200
    data = r.json()
    assert data["snapshot_run_id"] == str(snap)
    assert data["graph"]["nodes"][0]["id"] == "k8s:default:workload:api"
    assert data["data_quality"]["topology_confidence"] == "low"


def test_topology_endpoint_passes_run_id(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    snap = uuid4()

    def fake_fetch(settings, rid):
        assert rid == snap
        return snap, {"nodes": [], "edges": [], "meta": {"run_id": str(snap)}}, {}

    monkeypatch.setattr("agent.app.api.topology.fetch_topology_graph", fake_fetch)
    r = client.get(f"/v1/topology?run_id={snap}")

    assert r.status_code == 200
    assert r.json()["snapshot_run_id"] == str(snap)


def test_topology_endpoint_missing_snapshot_errors(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def no_snapshot(settings, rid):
        raise LookupError("no_snapshot")

    monkeypatch.setattr("agent.app.api.topology.fetch_topology_graph", no_snapshot)
    r = client.get("/v1/topology")

    assert r.status_code == 503


def test_topology_endpoint_unknown_run_errors(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def not_found(settings, rid):
        raise LookupError("run_not_found")

    monkeypatch.setattr("agent.app.api.topology.fetch_topology_graph", not_found)
    r = client.get(f"/v1/topology?run_id={uuid4()}")

    assert r.status_code == 404


def test_topology_endpoint_snapshot_database_errors_are_unavailable(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def unavailable(settings, rid):
        raise SQLAlchemyError("database offline")

    monkeypatch.setattr("agent.app.api.topology.fetch_topology_graph", unavailable)
    r = client.get("/v1/topology")

    assert r.status_code == 503


def test_namespace_allow_list_filters_visible_topology() -> None:
    graph = {
        "nodes": [
            {"id": "k8s:default:workload:api", "namespace": "default"},
            {"id": "k8s:kube-system:workload:dns", "namespace": "kube-system"},
            {"id": "external:postgres", "is_external": True},
        ],
        "edges": [
            {"id": "allowed", "from": "k8s:default:workload:api", "to": "external:postgres"},
            {"id": "excluded", "from": "k8s:kube-system:workload:dns", "to": "external:postgres"},
        ],
        "meta": {},
    }

    filtered = _filter_graph_namespaces(graph, {"default"})

    assert {node["id"] for node in filtered["nodes"]} == {"k8s:default:workload:api", "external:postgres"}
    assert [edge["id"] for edge in filtered["edges"]] == ["allowed"]
    assert filtered["meta"]["node_count"] == 2
    assert filtered["meta"]["edge_count"] == 1
