from __future__ import annotations

from typing import List

from agent.app.state import GraphState, PlanStep, Recommendation


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
    state["final_plan"] = build_plan(state.get("recommendations", []))
    return state

