"""
Recommendation agent: rank curated patterns into ``Recommendation`` records.

Only patterns explicitly mapped from observed smells (via ``SMELL_TO_PATTERN_MAP``)
receive recommendations—no free-form invention.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List

from agent.app.logging_utils import get_logger
from agent.app.models.pattern import ArchitecturePattern
from agent.app.services.analysis_scoping import copy_scope, scope_key
from agent.app.services.pattern_loader import SMELL_TO_PATTERN_MAP
from agent.app.state import GraphState, Recommendation

logger = get_logger("agent.nodes.recommend")

_IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3}
_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}
_CONF_SCORE = {"low": 1, "medium": 2, "high": 3}


def _pattern_score(p: ArchitecturePattern) -> float:
    """Heuristic sort key: reward impact and confidence, penalize effort (see weights)."""
    # Prefer high impact, low effort, high confidence.
    return (
        1.5 * _IMPACT_SCORE[p.impact]
        + 1.0 * _CONF_SCORE[p.confidence]
        - 1.0 * _EFFORT_SCORE[p.effort]
    )


def _pattern_priority(smell_types: List[str], pattern_id: str) -> tuple[int, str]:
    """Best (lowest) priority and reason string from ``SMELL_TO_PATTERN_MAP`` across smells."""
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
    """
    Score patterns, keep only those explicitly mapped from ``smell_types``, cap at ``limit``,
    then sort by mapping priority and impact/effort tie-breakers.
    """
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


def _mapped_pattern_ids(smell_types: List[str]) -> set[str]:
    mapped_ids: set[str] = set()
    for smell_type in smell_types:
        for mapped in SMELL_TO_PATTERN_MAP.get(smell_type, []):
            mapped_ids.add(mapped["pattern"])
    return mapped_ids


def _source_smell_ids(scoped_smells: List[Dict[str, Any]], pattern_id: str) -> List[str]:
    source_ids: List[str] = []
    for smell in scoped_smells:
        smell_type = str(smell.get("type") or "")
        if any(mapped["pattern"] == pattern_id for mapped in SMELL_TO_PATTERN_MAP.get(smell_type, [])):
            source_ids.append(str(smell.get("id") or smell_type))
    return source_ids


def recommend_for_scoped_smells(
    patterns: List[ArchitecturePattern],
    smells: List[Dict[str, Any]],
    limit_per_scope: int = 6,
) -> List[Recommendation]:
    """Generate recommendations independently for each analysis scope."""
    pattern_by_id = {pattern.id: pattern for pattern in patterns}
    smell_groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for smell in smells:
        key = scope_key(smell.get("scope"))
        smell_groups.setdefault(key, []).append(smell)

    recommendations: List[Recommendation] = []
    for group_smells in smell_groups.values():
        scope = copy_scope(group_smells[0].get("scope"))
        smell_types = [str(smell.get("type") or "") for smell in group_smells]
        scoped_patterns = [
            pattern_by_id[pattern_id]
            for pattern_id in _mapped_pattern_ids(smell_types)
            if pattern_id in pattern_by_id
        ]
        for rec in recommend_for_patterns(scoped_patterns, smell_types, limit=limit_per_scope):
            recommendations.append(
                rec.model_copy(
                    update={
                        "id": f"{scope.id}:{rec.pattern}",
                        "scope": scope,
                        "source_smells": _source_smell_ids(group_smells, rec.pattern),
                    }
                )
            )
    return recommendations


def recommend_node(state: GraphState) -> GraphState:
    """
    Recommendation node: convert curated patterns into concrete recommendations.
    """

    run_id = state.get("run_id", "n/a")
    smells = state.get("smells", [])
    smell_types = [s.get("type", "") for s in smells]
    logger.info(
        "recommendation_agent start run_id=%s patterns=%d smell_types=%s",
        run_id,
        len(state.get("patterns", [])),
        smell_types,
    )
    if any(s.get("scope") for s in smells):
        state["recommendations"] = recommend_for_scoped_smells(state.get("patterns", []), smells)
    else:
        state["recommendations"] = recommend_for_patterns(state.get("patterns", []), smell_types)
    logger.info(
        "recommendation_agent done run_id=%s recommendations=%s",
        run_id,
        [f"{r.pattern}(p{r.priority})" for r in state.get("recommendations", [])],
    )
    return state
