"""
Planner agent: turn ordered recommendations into ``PlanStep`` items for execution.

MVP ordering is heuristic (impact/effort buckets); richer dependency graphs can
replace ``build_plan`` later without changing the graph shape.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List

from agent.app.logging_utils import get_logger
from agent.app.services.analysis_scoping import build_scoped_analysis, scope_key
from agent.app.state import GraphState, PlanStep, Recommendation

logger = get_logger("agent.nodes.planner")

_IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3}
_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}


def _rank_bucket(rec: Recommendation) -> int:
    """Coarse bucket 0..2 for ``build_plan`` ordering (high impact / low effort first)."""
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
        rec_id = rec.id or f"{rec.pattern}:{idx}"
        steps.append(
            PlanStep(
                id=f"{rec_id}:step-{idx}",
                title=f"Step {idx}: Apply {rec.pattern}",
                description=f"{rec.solution} (Why: {rec.reason or 'Mapped from detected smell'})",
                impact=rec.impact,
                effort=rec.effort,
                depends_on=[],
                scope=rec.scope,
                recommendation_id=rec.id,
            )
        )
    return steps


def build_scoped_plan(recommendations: List[Recommendation]) -> List[PlanStep]:
    """Build independent plan sequences per analysis scope."""
    groups: "OrderedDict[str, List[Recommendation]]" = OrderedDict()
    for rec in recommendations:
        groups.setdefault(scope_key(rec.scope), []).append(rec)

    plan: List[PlanStep] = []
    for group in groups.values():
        plan.extend(build_plan(group))
    return plan


def planner_node(state: GraphState) -> GraphState:
    """Populate ``final_plan`` from current ``recommendations`` via ``build_plan``."""
    run_id = state.get("run_id", "n/a")
    logger.info(
        "planner_agent start run_id=%s recommendations=%d",
        run_id,
        len(state.get("recommendations", [])),
    )
    recommendations = state.get("recommendations", [])
    if any(getattr(rec, "scope", None) for rec in recommendations):
        state["final_plan"] = build_scoped_plan(recommendations)
    else:
        state["final_plan"] = build_plan(recommendations)
    state["scoped_analysis"] = build_scoped_analysis(
        state.get("smells", []),
        state.get("recommendations", []),
        state.get("critiques", []),
        state.get("final_plan", []),
    )
    logger.info(
        "planner_agent done run_id=%s plan_steps=%d first_step=%s",
        run_id,
        len(state.get("final_plan", [])),
        state.get("final_plan", [None])[0].title if state.get("final_plan") else "none",
    )
    return state
