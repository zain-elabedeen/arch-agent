from __future__ import annotations

from typing import Dict, List, Tuple

from agent.app.state import GraphState, PlanStep, Recommendation


_IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3}
_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}


def _priority(rec: Recommendation) -> float:
    # Higher is better: impact high, effort low.
    return (2.0 * _IMPACT_SCORE[rec.impact]) - (1.0 * _EFFORT_SCORE[rec.effort])


def build_plan(recommendations: List[Recommendation]) -> List[PlanStep]:
    """
    MVP plan: order by a simple impact/effort heuristic and generate steps.

    Dependencies are kept minimal; later versions can add graph-based ordering
    (e.g. "add timeouts" before "increase concurrency") using topology.
    """

    ordered = sorted(recommendations, key=_priority, reverse=True)
    steps: List[PlanStep] = []
    for rec in ordered:
        steps.append(
            PlanStep(
                title=f"Apply pattern: {rec.pattern_name}",
                description=(
                    rec.summary
                    + (" " if rec.solutions else "")
                    + ("Next actions: " + "; ".join(rec.solutions[:3]) if rec.solutions else "")
                ).strip(),
                impact=rec.impact,
                effort=rec.effort,
                depends_on=[],
            )
        )
    return steps


def planner_node(state: GraphState) -> GraphState:
    state["final_plan"] = build_plan(state.get("recommendations", []))
    return state

