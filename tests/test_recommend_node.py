from agent.app.config import Settings
from agent.app.nodes.recommend import recommend_node
from agent.app.services.analysis_scoping import scope_smells
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


def test_recommendations_are_deduped_per_scope_not_globally():
    store = load_pattern_store(Settings())
    patterns = [store.get_by_id("horizontal_scaling"), store.get_by_id("load_balancing")]
    topology = {
        "graph": {
            "nodes": [
                {"id": "k8s:default:workload:api", "name": "api", "namespace": "default", "is_external": False},
                {"id": "k8s:default:workload:worker", "name": "worker", "namespace": "default", "is_external": False},
            ],
            "edges": [],
            "meta": {},
        }
    }
    smells = scope_smells(
        [
            {
                "type": "single_instance_risk",
                "severity": "medium",
                "confidence": 0.74,
                "evidence": {"services": "api, worker", "single_instance_service_count": 2.0},
            }
        ],
        topology,
    )
    state = {
        "signals": {"single_instance_service_count": 2.0},
        "topology": topology,
        "smells": smells,
        "patterns": [p for p in patterns if p is not None],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
    }

    recs = recommend_node(state)["recommendations"]

    assert len(recs) == 4
    assert sum(1 for rec in recs if rec.pattern == "horizontal_scaling") == 2
    assert {rec.scope.node_id for rec in recs} == {
        "k8s:default:workload:api",
        "k8s:default:workload:worker",
    }
