from __future__ import annotations

from fastapi import FastAPI

from agent.app.config import get_settings
from agent.app.graph import build_graph
from agent.app.state import GraphState, RecommendationRequest, RecommendationResponse


app = FastAPI(title="ArchAgent", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/v1/recommendations", response_model=RecommendationResponse)
def recommend(req: RecommendationRequest) -> RecommendationResponse:
    settings = get_settings()
    graph = build_graph(settings)

    state: GraphState = {
        "raw_signals": dict(req.signals),
        "raw_topology": req.topology.model_dump(by_alias=True),
        "signals": {},
        "topology": {},
        "smells": [],
        "patterns": [],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
    }
    out = graph.invoke(state)

    return RecommendationResponse(
        smells=out.get("smells", []),
        recommendations=out.get("recommendations", []),
        critiques=out.get("critiques", []),
        plan=out.get("final_plan", []),
    )

