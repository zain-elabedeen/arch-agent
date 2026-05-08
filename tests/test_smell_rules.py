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


def test_detect_smells_includes_kubernetes_native_smells():
    smells = detect_smells(
        {
            "memory_utilization": 0.94,
            "pod_restart_total": 5,
            "unavailable_replicas": 2,
            "single_instance_service_count": 1,
            "hpa_scaling_pressure": 1.4,
        },
        {"edges": []},
    )

    smell_types = {s["type"] for s in smells}
    assert "memory_pressure" in smell_types
    assert "restart_instability" in smell_types
    assert "replica_unavailability" in smell_types
    assert "single_instance_risk" in smell_types
    assert "autoscaling_pressure" in smell_types


def test_detect_smells_includes_affected_services_from_topology_details():
    smells = detect_smells(
        {"single_instance_service_count": 1},
        {
            "edges": [],
            "service_details": {
                "demo-api": {"replicas": 1, "restarts": 0},
                "worker": {"replicas": 2, "restarts": 0},
            },
        },
    )

    single_instance = next(s for s in smells if s["type"] == "single_instance_risk")
    assert single_instance["evidence"]["services"] == "demo-api"


def test_detect_smells_includes_log_backed_smells():
    smells = detect_smells(
        {
            "error_rate": 0.08,
            "status_5xx_rate": 0.05,
            "request_count": 100,
            "timeout_count": 4,
            "dependency_error_count": 2,
            "probe_failure_count": 1,
            "crash_signal_count": 1,
        },
        {
            "service_details": {
                "test-api": {
                    "log_summary": {
                        "error_rate": 0.08,
                        "status_5xx_rate": 0.05,
                        "timeout_count": 4,
                        "dependency_error_count": 2,
                        "probe_failure_count": 1,
                        "crash_signal_count": 1,
                    }
                }
            }
        },
    )

    smell_types = {s["type"] for s in smells}
    assert "error_burst" in smell_types
    assert "timeout_pressure" in smell_types
    assert "dependency_instability" in smell_types
    assert "probe_instability" in smell_types
    assert "crash_loop_signal" in smell_types

    timeout = next(s for s in smells if s["type"] == "timeout_pressure")
    assert timeout["evidence"]["services"] == "test-api"
