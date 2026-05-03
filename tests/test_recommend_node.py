from agent.app.config import Settings
from agent.app.nodes.recommend import recommend_node
from agent.app.services.pattern_loader import load_pattern_store


def test_recommendation_output_is_structured_and_grounded():
    store = load_pattern_store(Settings())
    patterns = [
        store.get_by_id("read_replicas"),
        store.get_by_id("caching"),
        store.get_by_id("load_balancing"),
    ]
    state = {
        "signals": {"db_latency_p95_ms": 400, "request_latency_p95_ms": 800},
        "topology": {},
        "smells": [{"type": "read_scaling_bottleneck"}],
        "patterns": [p for p in patterns if p is not None],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
    }

    out = recommend_node(state)
    recs = out["recommendations"]

    assert len(recs) > 0
    first = recs[0]
    assert first.pattern in {"read_replicas", "caching", "load_balancing"}
    assert isinstance(first.solution, str) and first.solution
    assert first.impact in {"low", "medium", "high"}
    assert first.effort in {"low", "medium", "high"}
