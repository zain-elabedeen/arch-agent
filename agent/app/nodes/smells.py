"""Smell agent: run deterministic ``detect_smells`` on normalized state."""

from __future__ import annotations

from agent.app.logging_utils import get_logger
from agent.app.services.analysis_scoping import scope_smells
from agent.app.services.smell_rules import detect_smells
from agent.app.state import GraphState

logger = get_logger("agent.nodes.smells")

def smells_node(state: GraphState) -> GraphState:
    """
    Smell node: deterministic rules over normalized signals + topology.
    """
    run_id = state.get("run_id", "n/a")
    logger.info(
        "smell_agent start run_id=%s signal_keys=%s",
        run_id,
        sorted((state.get("signals", {}) or {}).keys()),
    )
    raw_smells = detect_smells(
        metrics=state.get("signals", {}),
        topology=state.get("topology", {}),
    )
    state["smells"] = scope_smells(raw_smells, state.get("topology", {}))
    logger.info(
        "smell_agent done run_id=%s smells=%s",
        run_id,
        [s.get("type", "unknown") for s in state.get("smells", [])],
    )
    return state
