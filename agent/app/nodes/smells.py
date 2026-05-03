from __future__ import annotations

from agent.app.services.smell_rules import run_smell_rules
from agent.app.state import GraphState


def smells_node(state: GraphState) -> GraphState:
    """
    Smell node: deterministic rules over normalized signals + topology.
    """

    state.smells = run_smell_rules(state.signals, state.topology)
    return state

