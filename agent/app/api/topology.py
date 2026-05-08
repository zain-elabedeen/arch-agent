"""Topology graph API routes."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from agent.app.config import get_settings
from agent.app.services.topology_load import fetch_topology_graph
from agent.app.state import TopologyResponse

router = APIRouter()


@router.get("/v1/topology", response_model=TopologyResponse)
def get_topology(
    snapshot_run_id: Optional[UUID] = Query(
        None,
        alias="run_id",
        description="Return topology for a specific snapshot run (UUID). Omit to use the latest run.",
    ),
) -> TopologyResponse:
    """Return the persisted UI-ready topology graph without running agents or contacting Kubernetes."""
    get_settings.cache_clear()
    settings = get_settings()
    try:
        rid, graph, data_quality = fetch_topology_graph(settings, snapshot_run_id)
    except RuntimeError:
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
    return TopologyResponse(snapshot_run_id=str(rid), graph=graph, data_quality=data_quality)
