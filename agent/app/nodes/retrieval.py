from __future__ import annotations

from typing import Dict, List, Set

from agent.app.config import Settings
from agent.app.models.pattern import ArchitecturePattern
from agent.app.services.pattern_loader import load_patterns
from agent.app.state import GraphState


# MVP deterministic mapping: smell_type -> pattern tags to consider.
# This is the "grounding" layer that avoids invented solutions.
SMELL_TO_TAGS: Dict[str, Set[str]] = {
    "cpu_saturation": {"autoscaling", "right-sizing", "caching"},
    "queue_backlog": {"queue", "backpressure", "concurrency", "dlq"},
    "db_latency_hotspot": {"database", "caching", "read-scaling"},
    "request_latency_regression": {"caching", "timeouts", "bulkheads"},
    "coupling_risk": {"decoupling", "domain", "api-gateway"},
}


def _pattern_matches(smell_type: str, pattern: ArchitecturePattern) -> bool:
    tags = set(pattern.tags)
    required = SMELL_TO_TAGS.get(smell_type, set())
    if not required:
        return True
    return len(tags.intersection(required)) > 0


def retrieve_patterns_for_smells(patterns: List[ArchitecturePattern], smell_types: List[str]) -> List[ArchitecturePattern]:
    selected: Dict[str, ArchitecturePattern] = {}
    for st in smell_types:
        for p in patterns:
            if _pattern_matches(st, p):
                selected[p.id] = p
    return list(selected.values())


def retrieval_node(state: GraphState, settings: Settings) -> GraphState:
    """
    Retrieval node: load the curated pattern catalog and filter it based on smells.
    """

    all_patterns = load_patterns(settings)
    smell_types = [s.type for s in state.smells]
    state.patterns = retrieve_patterns_for_smells(all_patterns, smell_types)
    return state

