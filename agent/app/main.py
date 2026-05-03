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

    state = GraphState(raw_signals=req.signals, raw_topology=req.topology)
    # LangGraph returns a plain dict state by default; validate back into our
    # typed state model to keep API output stable and testable.
    out = GraphState.model_validate(graph.invoke(state.model_dump(by_alias=True)))

    return RecommendationResponse(
        smells=out.smells,
        recommendations=out.recommendations,
        critiques=out.critiques,
        plan=out.plan,
    )

