from __future__ import annotations

import uuid

from fastapi import FastAPI

from agent.app.config import get_settings
from agent.app.graph import build_graph
from agent.app.logging_utils import configure_logging, get_logger
from agent.app.state import GraphState, RecommendationRequest, RecommendationResponse


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("agent.api")
app = FastAPI(title="ArchAgent", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/v1/recommendations", response_model=RecommendationResponse)
def recommend(req: RecommendationRequest) -> RecommendationResponse:
    settings = get_settings()
    graph = build_graph(settings)
    run_id = str(uuid.uuid4())[:8]
    logger.info(
        "recommendation request started run_id=%s signals=%s topology_services=%d topology_edges=%d",
        run_id,
        sorted(req.signals.keys()),
        len(req.topology.services),
        len(req.topology.edges),
    )

    state: GraphState = {
        "run_id": run_id,
        "raw_signals": dict(req.signals),
        "raw_topology": req.topology.model_dump(by_alias=True),
        "signals": {},
        "topology": {},
        "smells": [],
        "patterns": [],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
        "explanation_report": "",
    }
    out = graph.invoke(state)
    logger.info(
        "recommendation request completed run_id=%s smells=%d patterns=%d recommendations=%d critiques=%d plan_steps=%d report_chars=%d",
        run_id,
        len(out.get("smells", [])),
        len(out.get("patterns", [])),
        len(out.get("recommendations", [])),
        len(out.get("critiques", [])),
        len(out.get("final_plan", [])),
        len(out.get("explanation_report", "")),
    )

    return RecommendationResponse(
        smells=out.get("smells", []),
        recommendations=out.get("recommendations", []),
        critiques=out.get("critiques", []),
        plan=out.get("final_plan", []),
        explanation_report=out.get("explanation_report", ""),
    )

