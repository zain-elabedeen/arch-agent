"""
Retrieval agent: load the pattern store and select patterns for each smell type.

Deduplication by pattern id avoids duplicate work for the critic and recommender.
"""

from __future__ import annotations

from typing import Dict, List

from agent.app.config import Settings
from agent.app.logging_utils import get_logger
from agent.app.models.pattern import ArchitecturePattern
from agent.app.services.pattern_loader import PatternStore, load_pattern_store
from agent.app.state import GraphState

logger = get_logger("agent.nodes.retrieval")

def retrieve_patterns_for_smells(store: PatternStore, smell_types: List[str]) -> List[ArchitecturePattern]:
    """Union of patterns for all smell types, de-duplicated by pattern ``id``."""
    selected: Dict[str, ArchitecturePattern] = {}
    for st in smell_types:
        for p in store.get_patterns_for_smell(st):
            selected[p.id] = p
    return list(selected.values())


def retrieval_node(state: GraphState, settings: Settings) -> GraphState:
    """
    Retrieval node: load the curated pattern catalog and filter it based on smells.
    """

    run_id = state.get("run_id", "n/a")
    store = load_pattern_store(settings)
    smell_types = [s.get("type", "") for s in state.get("smells", [])]
    logger.info(
        "retrieval_agent start run_id=%s smell_types=%s catalog_size=%d",
        run_id,
        smell_types,
        len(store.get_all()),
    )
    state["patterns"] = retrieve_patterns_for_smells(store, smell_types)
    logger.info(
        "retrieval_agent done run_id=%s selected_patterns=%s",
        run_id,
        [p.id for p in state.get("patterns", [])],
    )
    return state

