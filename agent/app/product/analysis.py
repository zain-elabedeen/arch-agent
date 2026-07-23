"""Asynchronous analysis-run execution for the private worker."""

from __future__ import annotations

from typing import Any

from agent.app.config import get_settings
from agent.app.graph import build_graph
from agent.app.product.store import ProductStore, get_product_store
from agent.app.state import GraphState


def process_analysis_run(
    organization_id: str,
    run_id: str,
    *,
    store: ProductStore | None = None,
) -> dict[str, Any]:
    store = store or get_product_store()
    run = store.get_analysis_run(organization_id, run_id)
    if not run:
        raise LookupError("analysis_run_not_found")
    payload = dict(run.get("input_payload") or {})
    state: GraphState = {
        "run_id": run_id,
        "organization_id": organization_id,
        "raw_signals": dict(payload.get("signals") or {}),
        "raw_topology": dict(payload.get("topology") or {}),
        "raw_logs": dict(payload.get("logs") or {}),
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
        out = build_graph(get_settings()).invoke(state)
        result = {
            "smells": out.get("smells", []),
            "recommendations": out.get("recommendations", []),
            "critiques": out.get("critiques", []),
            "plan": out.get("final_plan", []),
            "explanation_source": out.get("explanation_source", ""),
            "explanation_report": out.get("explanation_report", ""),
        }
        return store.complete_analysis_run(organization_id, run_id, status="completed", result_payload=result) or {}
    except Exception as exc:
        store.complete_analysis_run(
            organization_id,
            run_id,
            status="failed",
            result_payload={"error_type": type(exc).__name__},
        )
        raise
