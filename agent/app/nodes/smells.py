from __future__ import annotations

from agent.app.services.smell_rules import detect_smells
from agent.app.state import GraphState


def smells_node(state: GraphState) -> GraphState:
    """
    Smell node: deterministic rules over normalized signals + topology.
    """
    state["smells"] = detect_smells(
        metrics=state.get("signals", {}),
        topology=state.get("topology", {}),
    )
    return state

