"""Tenant-scoped hosted snapshot readers."""

from __future__ import annotations

from typing import Any

from agent.app.connectors.kubernetes.topology_graph_builder import build_topology_graph, topology_graph_data_quality
from agent.app.product.store import ProductStore


def get_hosted_snapshot(
    store: ProductStore,
    organization_id: str,
    cluster_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    row = (
        store.load_cluster_snapshot(organization_id, cluster_id, run_id)
        if run_id
        else store.load_latest_cluster_snapshot(organization_id, cluster_id)
    )
    if not row:
        raise LookupError("run_not_found" if run_id else "no_snapshot")
    return row


def hosted_snapshot_raw(row: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any], dict[str, Any], str]:
    snapshot = dict(row.get("snapshot") or {})
    signals = {
        str(key): float(value)
        for key, value in (snapshot.get("signals") or {}).items()
        if isinstance(value, (int, float, bool))
    }
    logs = dict(snapshot.get("logs") or {})
    for key, value in (logs.get("signals") or {}).items():
        if isinstance(value, (int, float, bool)):
            signals.setdefault(str(key), float(value))
    topology = dict(snapshot.get("topology") or {})
    topology.setdefault("services", [str(item.get("name")) for item in snapshot.get("services") or [] if item.get("name")])
    topology.setdefault("edges", [])
    topology.setdefault("service_details", {})
    return signals, topology, logs, str(row["id"])


def hosted_topology_graph(row: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    snapshot = dict(row.get("snapshot") or {})
    topology = dict(snapshot.get("topology") or {})
    data_quality = dict(snapshot.get("data_quality") or {})
    graph = topology.get("graph") if isinstance(topology.get("graph"), dict) else None
    if not graph:
        graph = build_topology_graph(snapshot.get("services") or [], topology, data_quality, run_id=str(row["id"]))
    data_quality.update(
        topology_graph_data_quality(
            graph,
            missing_labels=int(data_quality.get("pods_without_app_label") or data_quality.get("topology_missing_labels") or 0),
        )
    )
    return str(row["id"]), dict(graph), data_quality
