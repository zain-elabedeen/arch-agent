from agent.app.models.pattern import ArchitecturePattern, PatternConstraint
from agent.app.nodes.critic import critic_node


def _base_state():
    return {
        "raw_signals": {},
        "raw_topology": {},
        "signals": {},
        "topology": {},
        "smells": [],
        "patterns": [],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
    }


def test_critic_generates_warning_from_phrase_based_avoid_when():
    pattern = ArchitecturePattern(
        id="read_replicas",
        name="Read Replicas",
        category="scaling",
        summary="Scale reads",
        avoid_when=["write-heavy workload"],
        solutions=["add replicas"],
        tradeoffs=["lag"],
        impact="high",
        effort="medium",
        confidence="high",
    )
    state = _base_state()
    state["signals"] = {"write_ratio": 0.8}
    state["patterns"] = [pattern]

    out = critic_node(state)

    assert len(out["critiques"]) == 1
    assert out["critiques"][0].pattern_id == "read_replicas"
    assert "write-heavy" in out["critiques"][0].message.lower()


def test_critic_generates_warning_from_structured_constraint():
    pattern = ArchitecturePattern(
        id="horizontal_scaling",
        name="Horizontal Scaling",
        category="scaling",
        summary="Scale out",
        avoid_when=[
            PatternConstraint(
                kind="signal",
                key="cpu_utilization",
                operator="gte",
                value=0.9,
                message="Resources are already saturated.",
            )
        ],
        solutions=["add instances"],
        tradeoffs=["cost"],
        impact="high",
        effort="medium",
        confidence="high",
    )
    state = _base_state()
    state["signals"] = {"cpu_utilization": 0.95}
    state["patterns"] = [pattern]

    out = critic_node(state)

    assert len(out["critiques"]) == 1
    assert out["critiques"][0].message == "Resources are already saturated."
