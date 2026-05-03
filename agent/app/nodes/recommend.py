from __future__ import annotations

from typing import Dict, List

from agent.app.logging_utils import get_logger
from agent.app.models.pattern import ArchitecturePattern
from agent.app.services.pattern_loader import SMELL_TO_PATTERN_MAP
from agent.app.state import GraphState, Recommendation

logger = get_logger("agent.nodes.recommend")

_IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3}
_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}
_CONF_SCORE = {"low": 1, "medium": 2, "high": 3}


def _pattern_score(p: ArchitecturePattern) -> float:
    # Prefer high impact, low effort, high confidence.
    return (
        1.5 * _IMPACT_SCORE[p.impact]
        + 1.0 * _CONF_SCORE[p.confidence]
        - 1.0 * _EFFORT_SCORE[p.effort]
    )


def _pattern_priority(smell_types: List[str], pattern_id: str) -> tuple[int, str]:
    best_priority = 999
    best_reason = ""
    for smell in smell_types:
        for mapped in SMELL_TO_PATTERN_MAP.get(smell, []):
            if mapped["pattern"] == pattern_id and mapped["priority"] < best_priority:
                best_priority = mapped["priority"]
                best_reason = mapped["reason"]
    return best_priority, best_reason


def recommend_for_patterns(
    patterns: List[ArchitecturePattern], smell_types: List[str], limit: int = 6
) -> List[Recommendation]:
    ordered = sorted(patterns, key=_pattern_score, reverse=True)
    recs: List[Recommendation] = []
    for p in ordered:
        priority, reason = _pattern_priority(smell_types, p.id)
        if priority == 999:
            # Recommendation agent stays grounded in smell->pattern mapping.
            continue
        solution = p.solutions[0] if p.solutions else p.summary
        recs.append(
            Recommendation(
                pattern=p.id,
                solution=solution,
                impact=p.impact,
                effort=p.effort,
                priority=priority,
                reason=reason,
            )
        )
        if len(recs) >= limit:
            break
    recs.sort(key=lambda r: (r.priority, -_IMPACT_SCORE[r.impact], _EFFORT_SCORE[r.effort]))
    return recs


def recommend_node(state: GraphState) -> GraphState:
    """
    Recommendation node: convert curated patterns into concrete recommendations.
    """

    run_id = state.get("run_id", "n/a")
    smell_types = [s.get("type", "") for s in state.get("smells", [])]
    logger.info(
        "recommendation_agent start run_id=%s patterns=%d smell_types=%s",
        run_id,
        len(state.get("patterns", [])),
        smell_types,
    )
    state["recommendations"] = recommend_for_patterns(state.get("patterns", []), smell_types)
    logger.info(
        "recommendation_agent done run_id=%s recommendations=%s",
        run_id,
        [f"{r.pattern}(p{r.priority})" for r in state.get("recommendations", [])],
    )
    return state

