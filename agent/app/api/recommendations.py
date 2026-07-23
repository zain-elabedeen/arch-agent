"""
Recommendation API routes.

Route handlers build the initial ``GraphState`` and invoke the compiled
LangGraph. Reasoning behavior remains in nodes/services.
"""

from __future__ import annotations

import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agent.app.config import get_settings
from agent.app.graph import build_graph
from agent.app.logging_utils import get_logger
from agent.app.product.auth import Identity, require_customer
from agent.app.product.csrf import require_csrf
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.snapshots import get_hosted_snapshot, hosted_snapshot_raw
from agent.app.services.snapshot_load import fetch_snapshot_raw
from agent.app.state import (
    GraphState,
    RecommendationRequest,
    RecommendationResponse,
    recommendation_request_has_inline_payload,
)

logger = get_logger("agent.api.recommendations")
router = APIRouter(tags=["Recommendations"])


@router.post(
    "/v1/recommendations",
    response_model=RecommendationResponse,
    dependencies=[Depends(require_csrf)],
    summary="Run Recommendations",
    description=(
        "Run the full recommendation pipeline and return smells, recommendations, critiques, plan steps, scoped analysis, "
        "log analysis, knowledge citations, and an explanation report. With an empty body, the API loads a persisted snapshot."
    ),
)
def recommend(
    req: RecommendationRequest = Body(default_factory=RecommendationRequest),
    snapshot_run_id: Optional[UUID] = Query(
        None,
        alias="run_id",
        description="Analyze a specific snapshot run (UUID). Omit to use the latest run.",
    ),
    cluster_id: str | None = Query(None, description="Associate the persisted analysis with an organization-owned cluster."),
    identity: Identity = Depends(require_customer),
    store: ProductStore = Depends(get_product_store),
) -> RecommendationResponse:
    """
    Run the full LangGraph pipeline once and return structured smells, recs,
    critiques, plan, and markdown explanation.

    With an empty body (no signals and no topology), loads inputs from the latest
    Kubernetes snapshot stored in Postgres.
    """
    # Refresh cached settings so .env changes apply without restarting uvicorn (dev UX).
    get_settings.cache_clear()
    settings = get_settings()
    graph = build_graph(settings)
    correlation_id = str(uuid.uuid4())[:8]
    organization_id = identity.organization_id or ""
    clusters = store.list_clusters(organization_id)
    active_cluster_id = cluster_id or (clusters[0]["id"] if clusters else None)
    active_cluster = store.get_cluster(organization_id, active_cluster_id) if active_cluster_id else None
    if active_cluster_id and not active_cluster:
        raise HTTPException(status_code=404, detail="Cluster not found.")

    if recommendation_request_has_inline_payload(req):
        raw_signals = dict(req.signals)
        raw_topology = req.topology.model_dump(by_alias=True)
        raw_logs = dict(req.logs)
        snapshot_db_id: Optional[str] = None
    else:
        try:
            if active_cluster and active_cluster["connection_mode"] == "helm":
                row = get_hosted_snapshot(store, organization_id, active_cluster_id, str(snapshot_run_id) if snapshot_run_id else None)
                raw_signals, raw_topology, raw_logs, snap = hosted_snapshot_raw(row)
            else:
                raw_signals, raw_topology, raw_logs, snap = fetch_snapshot_raw(settings, snapshot_run_id)
            snapshot_db_id = str(snap)
        except (RuntimeError, SQLAlchemyError):
            raise HTTPException(
                status_code=503,
                detail="Snapshot mode requires ARCHAGENT_POSTGRES_DSN and a populated runs table; "
                "send signals/topology in the body for inline analysis.",
            ) from None
        except LookupError as e:
            code = e.args[0] if e.args else ""
            if code == "no_snapshot":
                raise HTTPException(
                    status_code=503,
                    detail="No Kubernetes snapshot found yet; run the connector worker or POST inline signals.",
                ) from None
            if code == "run_not_found":
                raise HTTPException(status_code=404, detail="Snapshot run_id not found.") from None
            raise HTTPException(status_code=500, detail="Snapshot data incomplete.") from None

    persisted_run = store.create_analysis_run(
        organization_id,
        identity.user_id,
        active_cluster_id,
        {"snapshot_run_id": snapshot_db_id, "source": "recommendations"},
    )

    logger.info(
        "recommendation request started run_id=%s snapshot_db_id=%s signals=%s topology_services=%d topology_edges=%d",
        correlation_id,
        snapshot_db_id,
        sorted(raw_signals.keys()),
        len(raw_topology.get("services") or []),
        len(raw_topology.get("edges") or []),
    )

    state: GraphState = {
        "run_id": correlation_id,
        "organization_id": organization_id,
        "raw_signals": raw_signals,
        "raw_topology": raw_topology,
        "raw_logs": raw_logs,
        "signals": {},
        "topology": {},
        "smells": [],
        "patterns": [],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
        "scoped_analysis": [],
        "log_analysis": {},
        "knowledge_context": [],
        "explanation_source": "",
        "explanation_report": "",
    }
    try:
        out = graph.invoke(state)
        response = RecommendationResponse(
            snapshot_run_id=snapshot_db_id,
            smells=out.get("smells", []),
            recommendations=out.get("recommendations", []),
            critiques=out.get("critiques", []),
            plan=out.get("final_plan", []),
            scoped_analysis=out.get("scoped_analysis", []),
            log_analysis=out.get("log_analysis", {}),
            knowledge_context=out.get("knowledge_context", []),
            explanation_source=out.get("explanation_source", ""),
            explanation_report=out.get("explanation_report", ""),
        )
    except Exception as exc:
        store.complete_analysis_run(
            organization_id,
            persisted_run["id"],
            status="failed",
            result_payload={"snapshot_run_id": snapshot_db_id, "error_type": type(exc).__name__},
        )
        raise
    logger.info(
        "recommendation request completed run_id=%s smells=%d patterns=%d recommendations=%d critiques=%d plan_steps=%d report_chars=%d",
        correlation_id,
        len(out.get("smells", [])),
        len(out.get("patterns", [])),
        len(out.get("recommendations", [])),
        len(out.get("critiques", [])),
        len(out.get("final_plan", [])),
        len(out.get("explanation_report", "")),
    )
    store.complete_analysis_run(
        organization_id,
        persisted_run["id"],
        status="completed",
        result_payload={
            "snapshot_run_id": snapshot_db_id,
            "smell_count": len(out.get("smells", [])),
            "recommendation_count": len(out.get("recommendations", [])),
            "critique_count": len(out.get("critiques", [])),
            "plan_step_count": len(out.get("final_plan", [])),
            "explanation_source": out.get("explanation_source", ""),
        },
    )
    return response
