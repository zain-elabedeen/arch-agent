from __future__ import annotations

from typing import Dict, List

from agent.app.models.pattern import ArchitecturePattern
from agent.app.state import GraphState, Recommendation


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


def recommend_for_patterns(patterns: List[ArchitecturePattern], limit: int = 6) -> List[Recommendation]:
    ordered = sorted(patterns, key=_pattern_score, reverse=True)[:limit]
    recs: List[Recommendation] = []
    for p in ordered:
        recs.append(
            Recommendation(
                pattern_id=p.id,
                pattern_name=p.name,
                summary=p.summary,
                solutions=list(p.solutions),
                tradeoffs=list(p.tradeoffs),
                impact=p.impact,
                effort=p.effort,
                confidence=p.confidence,
            )
        )
    return recs


def recommend_node(state: GraphState) -> GraphState:
    """
    Recommendation node: convert curated patterns into concrete recommendations.
    """

    state["recommendations"] = recommend_for_patterns(state.get("patterns", []))
    return state

