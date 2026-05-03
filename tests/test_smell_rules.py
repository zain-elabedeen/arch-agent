from agent.app.services.smell_rules import detect_smells


def test_detect_smells_triggers_required_smells():
    metrics = {
        "db_latency_ms": 320,
        "request_latency_p95_ms": 700,
        "cpu": 0.95,
        "backlog": 15000,
    }
    topology = {
        "edges": [
            {"from": "api", "to": "svc-a", "type": "http"},
            {"from": "api", "to": "svc-b", "type": "http"},
            {"from": "api", "to": "svc-c", "type": "http"},
            {"from": "api", "to": "svc-d", "type": "http"},
        ]
    }

    smells = detect_smells(metrics, topology)
    smell_types = {s["type"] for s in smells}

    assert "read_scaling_bottleneck" in smell_types
    assert "cpu_saturation" in smell_types
    assert "queue_backlog" in smell_types
    assert "coupling_risk" in smell_types


def test_detect_smells_respects_thresholds():
    metrics = {
        "db_latency_ms": 200,
        "request_latency_p95_ms": 400,
        "cpu": 0.6,
        "backlog": 1000,
    }
    topology = {"edges": [{"from": "api", "to": "svc-a", "type": "http"}]}

    smells = detect_smells(metrics, topology)
    assert smells == []
