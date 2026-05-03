from __future__ import annotations

from typing import List

from agent.app.logging_utils import get_logger
from agent.app.state import GraphState, PlanStep, Recommendation

logger = get_logger("agent.nodes.planner")

_IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3}
_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}


def _rank_bucket(rec: Recommendation) -> int:
    # Requested ordering:
    # 1) high impact / low effort first
    # 2) medium impact / medium effort next
    if rec.impact == "high" and rec.effort == "low":
        return 0
    if rec.impact == "medium" and rec.effort == "medium":
        return 1
    return 2


def build_plan(recommendations: List[Recommendation]) -> List[PlanStep]:
    """
    MVP plan: order by a simple impact/effort heuristic and generate steps.

    Dependencies are kept minimal; later versions can add graph-based ordering
    (e.g. "add timeouts" before "increase concurrency") using topology.
    """

    ordered = sorted(
        recommendations,
        key=lambda r: (_rank_bucket(r), -_IMPACT_SCORE[r.impact], _EFFORT_SCORE[r.effort], r.priority),
    )
    steps: List[PlanStep] = []
    for idx, rec in enumerate(ordered, start=1):
        steps.append(
            PlanStep(
                title=f"Step {idx}: Apply {rec.pattern}",
                description=f"{rec.solution} (Why: {rec.reason or 'Mapped from detected smell'})",
                impact=rec.impact,
                effort=rec.effort,
                depends_on=[],
            )
        )
    return steps


def planner_node(state: GraphState) -> GraphState:
    run_id = state.get("run_id", "n/a")
    logger.info(
        "planner_agent start run_id=%s recommendations=%d",
        run_id,
        len(state.get("recommendations", [])),
    )
    state["final_plan"] = build_plan(state.get("recommendations", []))
    logger.info(
        "planner_agent done run_id=%s plan_steps=%d first_step=%s",
        run_id,
        len(state.get("final_plan", [])),
        state.get("final_plan", [None])[0].title if state.get("final_plan") else "none",
    )
    return state

