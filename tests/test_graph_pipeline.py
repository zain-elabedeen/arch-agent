from agent.app.config import Settings
from agent.app.graph import build_graph


def test_graph_pipeline_runs_end_to_end():
    graph = build_graph(Settings())
    state = {
        "raw_signals": {
            "db_latency_ms": 300,
            "request_latency_p95_ms": 650,
            "cpu": 0.93,
            "backlog": 12000,
            "error_rate": 0.08,
        },
        "raw_topology": {
            "services": ["api", "svc-a", "svc-b", "svc-c", "svc-d"],
            "edges": [
                {"from": "api", "to": "svc-a", "type": "http"},
                {"from": "api", "to": "svc-b", "type": "http"},
                {"from": "api", "to": "svc-c", "type": "http"},
                {"from": "api", "to": "svc-d", "type": "http"},
            ],
        },
        "signals": {},
        "topology": {},
        "smells": [],
        "patterns": [],
        "recommendations": [],
        "critiques": [],
        "final_plan": [],
        "explanation_report": "",
    }

    out = graph.invoke(state)

    assert "smells" in out and len(out["smells"]) > 0
    assert "patterns" in out and len(out["patterns"]) > 0
    assert "recommendations" in out and len(out["recommendations"]) > 0
    assert "critiques" in out
    assert "final_plan" in out and len(out["final_plan"]) > 0
    assert "explanation_report" in out and isinstance(out["explanation_report"], str) and len(out["explanation_report"]) > 0
