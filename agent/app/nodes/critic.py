from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.app.models.pattern import ArchitecturePattern, PatternConstraint
from agent.app.state import Critique, GraphState


def _get_signal_value(state: GraphState, key: str) -> Optional[float]:
    val = state.get("signals", {}).get(key)
    if val is None:
        # allow looking up raw metric keys too
        raw = state.get("raw_signals", {}).get(key)
        return float(raw) if raw is not None else None
    return float(val)


def _topology_has(state: GraphState, key: str) -> bool:
    topology = state.get("topology", {})
    edges = topology.get("edges", [])
    if key == "has_db_edge":
        return any(e.get("type") == "db" for e in edges)
    if key == "has_queue_edge":
        return any(e.get("type") == "queue" for e in edges)
    if key == "has_critical_store":
        return len(topology.get("critical_stores", [])) > 0
    if key == "has_critical_queue":
        return len(topology.get("critical_queues", [])) > 0
    return False


def _eval_constraint(state: GraphState, c: PatternConstraint) -> tuple[bool, Dict[str, Any]]:
    evidence: Dict[str, Any] = {"constraint_key": c.key, "operator": c.operator, "kind": c.kind}
    if c.kind == "topology":
        ok = _topology_has(state, c.key)
        evidence["value"] = ok
        # For topology constraints we treat "exists" as the meaningful operator.
        return ok if c.operator == "exists" else ok, evidence

    val = _get_signal_value(state, c.key)
    evidence["value"] = val
    if val is None:
        return False, evidence

    if c.operator == "exists":
        return True, evidence
    if c.value is None:
        return False, evidence

    if c.operator == "gt":
        return val > c.value, evidence
    if c.operator == "gte":
        return val >= c.value, evidence
    if c.operator == "lt":
        return val < c.value, evidence
    if c.operator == "lte":
        return val <= c.value, evidence
    if c.operator == "eq":
        return val == c.value, evidence
    if c.operator == "neq":
        return val != c.value, evidence

    return False, evidence


def critique_patterns(state: GraphState, patterns: List[ArchitecturePattern]) -> List[Critique]:
    critiques: List[Critique] = []
    for p in patterns:
        for c in p.avoid_when:
            triggered, evidence = _eval_constraint(state, c)
            if triggered:
                critiques.append(
                    Critique(
                        pattern_id=p.id,
                        level="warning",
                        message=c.message or f"Avoid constraint triggered for {p.name}: {c.key} {c.operator} {c.value}",
                        evidence={k: str(v) for k, v in evidence.items()},
                    )
                )
    return critiques


def critic_node(state: GraphState) -> GraphState:
    """
    Critic node: apply avoid_when constraints to surface risks/warnings.
    """

    state["critiques"] = critique_patterns(state, state.get("patterns", []))
    return state

