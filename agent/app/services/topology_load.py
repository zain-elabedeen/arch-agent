"""Load UI topology graph payloads from persisted connector snapshots."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from agent.app.config import Settings
from agent.app.connectors.kubernetes.topology_graph_builder import build_topology_graph, topology_graph_data_quality
from agent.app.connectors.repository import ensure_connector_schema, load_latest_snapshot, load_run_snapshot
from agent.app.services.snapshot_load import _engine_for


def _graph_from_snapshot(run_id: UUID, snapshot: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    topology = dict(snapshot.get("topology") or {})
    data_quality = dict(snapshot.get("data_quality") or {})
    graph = topology.get("graph") if isinstance(topology.get("graph"), dict) else None
    if not graph:
        graph = build_topology_graph(snapshot.get("services") or [], topology, data_quality, run_id=str(run_id))
    else:
        graph = dict(graph)
        meta = dict(graph.get("meta") or {})
        meta["run_id"] = str(run_id)
        graph["meta"] = meta
    data_quality.update(
        topology_graph_data_quality(
            graph,
            missing_labels=int(data_quality.get("pods_without_app_label") or data_quality.get("topology_missing_labels") or 0),
        )
    )
    return graph, data_quality


def fetch_topology_graph(settings: Settings, run_id: Optional[UUID]) -> Tuple[UUID, Dict[str, Any], Dict[str, Any]]:
    """Return ``(snapshot_run_id, graph, data_quality)`` for the latest or requested snapshot."""
    if not settings.postgres_dsn:
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is not set")
    eng = _engine_for(settings.postgres_dsn)
    if settings.k8s_auto_migrate:
        ensure_connector_schema(eng)
    with eng.connect() as conn:
        if run_id is not None:
            snapshot = load_run_snapshot(conn, run_id)
            if snapshot is None:
                raise LookupError("run_not_found")
            graph, data_quality = _graph_from_snapshot(run_id, snapshot)
            return run_id, graph, data_quality
        latest = load_latest_snapshot(conn)
        if latest is None:
            raise LookupError("no_snapshot")
        rid, snapshot = latest
        graph, data_quality = _graph_from_snapshot(rid, snapshot)
        return rid, graph, data_quality
