from agent.app.connectors.snapshot_merge import snapshot_with_kubernetes, snapshot_with_logs


def test_logs_worker_merges_log_signals_into_existing_snapshot():
    base_snapshot = {
        "signals": {"cpu_utilization": 0.3},
        "services": [
            {
                "name": "api",
                "namespace": "default",
                "cpu": 0.3,
                "memory": 0.2,
                "replicas": 2,
                "available_replicas": 2,
                "unavailable_replicas": 0,
                "restarts": 0,
            }
        ],
        "topology": {
            "services": ["api"],
            "edges": [],
            "service_details": {
                "api": {
                    "namespace": "default",
                    "replicas": 2,
                    "available_replicas": 2,
                    "restarts": 0,
                }
            },
        },
    }
    logs = {
        "events": [],
        "signals": {"request_count": 21.0, "request_latency_p95_ms": 200.0, "timeout_count": 1.0},
        "service_signals": {"api": {"request_count": 21.0, "timeout_count": 1.0}},
        "data_quality": {"logs_enabled": True},
    }

    out = snapshot_with_logs(base_snapshot, logs)

    assert out["signals"]["cpu_utilization"] == 0.3
    assert out["signals"]["request_latency_p95_ms"] == 200.0
    assert out["logs"]["data_quality"]["logs_enabled"] is True
    assert out["topology"]["service_details"]["api"]["log_summary"]["timeout_count"] == 1.0


def test_logs_worker_can_create_logs_only_snapshot_for_new_source_data():
    logs = {
        "events": [],
        "signals": {"request_count": 2.0, "error_count": 1.0},
        "service_signals": {"api": {"request_count": 2.0, "error_count": 1.0}},
        "data_quality": {"logs_enabled": True},
    }

    out = snapshot_with_logs(None, logs)

    assert out["signals"]["error_count"] == 1.0
    assert out["topology"]["services"] == ["api"]
    assert out["services"][0]["name"] == "api"
    assert out["services"][0]["replicas"] == 0


def test_kubernetes_snapshot_merge_preserves_existing_logs():
    base_snapshot = {
        "logs": {
            "events": [],
            "signals": {"timeout_count": 1.0},
            "service_signals": {"api": {"timeout_count": 1.0}},
            "data_quality": {"logs_enabled": True},
        }
    }
    kubernetes_snapshot = {
        "signals": {"cpu_utilization": 0.4},
        "services": [{"name": "api", "namespace": "default", "cpu": 0.4, "memory": 0.2, "replicas": 2, "restarts": 0}],
        "topology": {
            "services": ["api"],
            "edges": [],
            "service_details": {"api": {"namespace": "default", "replicas": 2}},
        },
    }

    out = snapshot_with_kubernetes(base_snapshot, kubernetes_snapshot)

    assert out["logs"]["signals"]["timeout_count"] == 1.0
    assert out["signals"]["cpu_utilization"] == 0.4
    assert out["signals"]["timeout_count"] == 1.0
    assert out["topology"]["service_details"]["api"]["log_summary"]["timeout_count"] == 1.0
