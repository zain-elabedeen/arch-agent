from __future__ import annotations

from agent.app.services.smell_rules import detect_smells
from agent.app.state import GraphState, Smell


def _bucket_confidence(value: float) -> str:
    if value >= 0.85:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def smells_node(state: GraphState) -> GraphState:
    """
    Smell node: deterministic rules over normalized signals + topology.
    """
    smell_dicts = detect_smells(
        metrics=state.signals.model_dump(exclude_none=True),
        topology=state.topology.model_dump(by_alias=True, exclude_none=True),
    )

    state.smells = [
        Smell(
            type=item["type"],
            severity=item.get("severity", "medium"),
            confidence=_bucket_confidence(float(item.get("confidence", 0.7))),
            evidence=item.get("evidence", {}),
        )
        for item in smell_dicts
    ]
    return state

