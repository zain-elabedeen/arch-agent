"""Topology graph API routes."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agent.app.config import get_settings
from agent.app.product.auth import Identity, require_customer
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.snapshots import get_hosted_snapshot, hosted_topology_graph
from agent.app.services.topology_load import fetch_topology_graph
from agent.app.state import TopologyResponse

router = APIRouter(tags=["Topology"])


def _filter_graph_namespaces(graph: dict[str, Any], monitored_namespaces: set[str]) -> dict[str, Any]:
    """Limit tenant-visible graph data to the selected cluster namespace allow-list."""
    nodes = [dict(node) for node in graph.get("nodes") or []]
    visible_nodes = [
        node
        for node in nodes
        if node.get("is_external") or not node.get("namespace") or node.get("namespace") in monitored_namespaces
    ]
    visible_ids = {str(node["id"]) for node in visible_nodes}
    edges = [
        dict(edge)
        for edge in graph.get("edges") or []
        if edge.get("from") in visible_ids and edge.get("to") in visible_ids
    ]
    connected_ids = {str(edge["from"]) for edge in edges} | {str(edge["to"]) for edge in edges}
    visible_nodes = [node for node in visible_nodes if not node.get("is_external") or node.get("id") in connected_ids]
    filtered = dict(graph)
    filtered["nodes"] = visible_nodes
    filtered["edges"] = edges
    filtered["meta"] = {**dict(graph.get("meta") or {}), "node_count": len(visible_nodes), "edge_count": len(edges)}
    return filtered


def _graph_workload_namespaces(graph: dict[str, Any]) -> set[str]:
    return {
        str(node["namespace"])
        for node in graph.get("nodes") or []
        if node.get("namespace") and not node.get("is_external")
    }


@router.get(
    "/v1/topology",
    response_model=TopologyResponse,
    summary="Get Topology Graph",
    description="Return the persisted UI-ready topology graph for the active organization's selected cluster.",
)
def get_topology(
    snapshot_run_id: Optional[UUID] = Query(
        None,
        alias="run_id",
        description="Return topology for a specific snapshot run (UUID). Omit to use the latest run.",
    ),
    cluster_id: str | None = Query(None, description="Use this organization-owned cluster context. Defaults to the first cluster."),
    identity: Identity = Depends(require_customer),
    store: ProductStore = Depends(get_product_store),
) -> TopologyResponse:
    """Return the persisted UI-ready topology graph without running agents or contacting Kubernetes."""
    get_settings.cache_clear()
    settings = get_settings()
    clusters = store.list_clusters(identity.organization_id or "")
    active_cluster_id = cluster_id or (clusters[0]["id"] if clusters else None)
    cluster = store.get_cluster(identity.organization_id or "", active_cluster_id) if active_cluster_id else None
    if not active_cluster_id or not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found.")
    try:
        if cluster["connection_mode"] == "helm":
            row = get_hosted_snapshot(
                store,
                identity.organization_id or "",
                active_cluster_id,
                str(snapshot_run_id) if snapshot_run_id else None,
            )
            rid, graph, data_quality = hosted_topology_graph(row)
        else:
            rid, graph, data_quality = fetch_topology_graph(settings, snapshot_run_id)
    except (RuntimeError, SQLAlchemyError):
        raise HTTPException(
            status_code=503,
            detail="Topology snapshot mode requires ARCHAGENT_POSTGRES_DSN and a populated runs table.",
        ) from None
    except LookupError as e:
        code = e.args[0] if e.args else ""
        if code == "run_not_found":
            raise HTTPException(status_code=404, detail="Snapshot run_id not found.") from None
        if code == "no_snapshot":
            raise HTTPException(status_code=503, detail="No topology snapshot found yet; run the connector worker.") from None
        raise HTTPException(status_code=500, detail="Topology snapshot data incomplete.") from None
    store.sync_discovered_namespaces(
        identity.organization_id or "",
        active_cluster_id,
        _graph_workload_namespaces(graph),
        {str(namespace) for namespace in data_quality.get("excluded_namespaces") or []},
    )
    monitored_namespaces = {
        item["namespace"]
        for item in store.list_namespaces(identity.organization_id or "", active_cluster_id)
        if item["monitored"]
    }
    graph = _filter_graph_namespaces(graph, monitored_namespaces)
    data_quality = {**data_quality, "selected_cluster_id": active_cluster_id, "monitored_namespaces": sorted(monitored_namespaces)}
    return TopologyResponse(snapshot_run_id=str(rid), graph=graph, data_quality=data_quality)
