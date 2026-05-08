from agent.app.connectors.kubernetes.topology_graph_builder import (
    build_topology_graph,
    external_node_id,
    stable_edge_id,
    topology_graph_data_quality,
    workload_node_id,
)


def test_stable_node_and_edge_ids():
    src = workload_node_id("default", "api")
    tgt = workload_node_id("default", "worker")

    assert src == "k8s:default:workload:api"
    assert external_node_id("database", "orders-db") == "external:database:orders-db"
    assert stable_edge_id(src, tgt, "queue") == "k8s:default:workload:api->k8s:default:workload:worker:queue"


def test_build_topology_graph_projects_services_edges_evidence_and_external_nodes():
    services = [
        {
            "name": "api",
            "namespace": "default",
            "cpu": 0.4,
            "memory": 0.3,
            "cpu_usage_cores": 0.2,
            "memory_usage_bytes": 1000,
            "replicas": 2,
            "available_replicas": 2,
            "unavailable_replicas": 0,
            "restarts": 0,
        },
        {
            "name": "worker",
            "namespace": "default",
            "cpu": 0.2,
            "memory": 0.2,
            "cpu_usage_cores": 0.1,
            "memory_usage_bytes": 500,
            "replicas": 1,
            "available_replicas": 1,
            "unavailable_replicas": 0,
            "restarts": 0,
        },
    ]
    topology = {
        "services": ["api", "worker"],
        "edges": [
            {
                "from": "api",
                "to": "worker",
                "type": "queue",
                "inferred_from": "annotation,env_dns",
                "confidence": 0.95,
                "evidence": ["archagent.io/depends-on=queue:worker", "QUEUE_URL=worker.default.svc"],
            }
        ],
        "external_edges": [
            {
                "from": "api",
                "to": "api.stripe.com",
                "type": "http",
                "inferred_from": "external_hostname",
                "confidence": 0.45,
                "evidence": ["PAYMENTS_URL=https://api.stripe.com"],
                "protocol": "https",
            }
        ],
        "service_details": {},
    }

    graph = build_topology_graph(services, topology, {}, generated_at="2026-05-08T00:00:00+00:00")

    assert graph["meta"]["node_count"] == 3
    assert graph["meta"]["edge_count"] == 2
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    assert "k8s:default:workload:api" in nodes_by_id
    assert "external:external_service:api.stripe.com" in nodes_by_id
    internal_edge = next(edge for edge in graph["edges"] if edge["type"] == "queue")
    assert internal_edge["confidence"] == 0.95
    assert internal_edge["inferred_from"] == ["annotation", "env_dns"]
    assert "QUEUE_URL=worker.default.svc" in internal_edge["evidence"]
    external_edge = next(edge for edge in graph["edges"] if edge["to"] == "external:external_service:api.stripe.com")
    assert external_edge["confidence"] == 0.45
    assert external_edge["protocol"] == "https"

    quality = topology_graph_data_quality(graph)
    assert quality["topology_external_nodes"] == 1
    assert quality["topology_edges_low_confidence"] == 1


def test_node_status_uses_runtime_and_log_summary_signals():
    services = [
        {
            "name": "api",
            "namespace": "default",
            "replicas": 2,
            "available_replicas": 2,
            "unavailable_replicas": 0,
            "restarts": 3,
            "cpu": 0.2,
            "memory": 0.2,
            "cpu_usage_cores": 0.1,
            "memory_usage_bytes": 1000,
        },
        {
            "name": "worker",
            "namespace": "default",
            "replicas": 1,
            "available_replicas": 1,
            "unavailable_replicas": 0,
            "restarts": 0,
            "cpu": 0.2,
            "memory": 0.2,
            "cpu_usage_cores": 0.1,
            "memory_usage_bytes": 1000,
        },
    ]
    topology = {
        "services": ["api", "worker"],
        "edges": [],
        "service_details": {
            "worker": {
                "namespace": "default",
                "replicas": 1,
                "available_replicas": 1,
                "unavailable_replicas": 0,
                "restarts": 0,
                "log_summary": {"error_rate": 0.08, "request_latency_p95_ms": 900, "request_count": 50},
            }
        },
    }

    graph = build_topology_graph(services, topology, {})
    nodes = {node["name"]: node for node in graph["nodes"]}

    assert nodes["api"]["status"] == "degraded"
    assert nodes["worker"]["status"] == "degraded"
    assert nodes["worker"]["data_sources"] == ["kubernetes", "logs"]
    assert nodes["worker"]["request_count"] == 50.0
