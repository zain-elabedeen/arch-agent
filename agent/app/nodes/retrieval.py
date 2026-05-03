from __future__ import annotations

from typing import Dict, List

from agent.app.config import Settings
from agent.app.models.pattern import ArchitecturePattern
from agent.app.services.pattern_loader import PatternStore, load_pattern_store
from agent.app.state import GraphState


def retrieve_patterns_for_smells(store: PatternStore, smell_types: List[str]) -> List[ArchitecturePattern]:
    selected: Dict[str, ArchitecturePattern] = {}
    for st in smell_types:
        for p in store.get_patterns_for_smell(st):
            selected[p.id] = p
    return list(selected.values())


def retrieval_node(state: GraphState, settings: Settings) -> GraphState:
    """
    Retrieval node: load the curated pattern catalog and filter it based on smells.
    """

    store = load_pattern_store(settings)
    smell_types = [s.type for s in state.smells]
    state.patterns = retrieve_patterns_for_smells(store, smell_types)
    return state

