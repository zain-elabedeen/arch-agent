"""Build UI-ready topology graphs from canonical Kubernetes snapshots."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

_K8S_PREFIX = "k8s"

_DB_HINTS = ("postgres", "mysql", "mongo", "elastic", "cockroach", "clickhouse", "mariadb", "database", "db")
_CACHE_HINTS = ("redis", "memcached", "cache")
_QUEUE_HINTS = ("kafka", "rabbit", "nats", "sqs", "queue", "worker", "broker", "stream")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", value.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-.")
    return slug or "unknown"


def workload_node_id(namespace: str | None, service_name: str) -> str:
    """Stable graph ID for one Kubernetes logical workload node."""
    return f"{_K8S_PREFIX}:{_slug(namespace or 'unknown')}:workload:{_slug(service_name)}"


def external_node_id(kind: str, name: str) -> str:
    """Stable graph ID for one unresolved external dependency node."""
    return f"external:{_slug(kind)}:{_slug(name)}"


def stable_edge_id(from_node: str, to_node: str, edge_type: str) -> str:
    """Stable graph ID for one directed dependency edge."""
    return f"{from_node}->{to_node}:{_slug(edge_type or 'unknown')}"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_node_kind(name: str, edge_type: str | None = None, external: bool = False) -> str:
    text = f"{name} {edge_type or ''}".lower()
    if any(x in text for x in _CACHE_HINTS) or edge_type == "cache":
        return "cache"
    if any(x in text for x in _DB_HINTS) or edge_type == "db":
        return "database"
    if any(x in text for x in _QUEUE_HINTS) or edge_type in {"queue", "stream"}:
        return "queue"
    if external:
        return "external_service"
    return "workload"


def _log_summary(detail: Dict[str, Any]) -> Dict[str, Any]:
    log_summary = detail.get("log_summary")
    return log_summary if isinstance(log_summary, dict) else {}


def _node_status(detail: Dict[str, Any]) -> tuple[str, str]:
    log = _log_summary(detail)
    replicas = _as_int(detail.get("replicas"))
    available = _as_int(detail.get("available_replicas"))
    unavailable = _as_int(detail.get("unavailable_replicas")) or 0
    restarts = _as_int(detail.get("restarts")) or 0
    cpu = _as_float(detail.get("cpu"))
    memory = _as_float(detail.get("memory"))
    error_rate = _as_float(log.get("error_rate")) or 0.0
    status_5xx_rate = _as_float(log.get("status_5xx_rate")) or 0.0
    timeout_count = _as_float(log.get("timeout_count")) or 0.0
    crash_count = _as_float(log.get("crash_signal_count")) or 0.0
    oom_count = _as_float(log.get("oom_killed_count")) or 0.0

    if (unavailable > 0 and available == 0) or crash_count > 0 or oom_count > 0:
        return "critical", "critical"
    if (
        unavailable > 0
        or restarts >= 3
        or error_rate > 0.05
        or status_5xx_rate > 0.03
        or timeout_count >= 3
        or (cpu is not None and cpu > 0.9)
        or (memory is not None and memory > 0.9)
    ):
        return "degraded", "high"
    if replicas is None and cpu is None and memory is None and not log:
        return "unknown", "none"
    return "healthy", "none"


def _merge_detail(service: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(service or {})
    merged.update(detail or {})
    return merged


def _source_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return sorted({str(v).strip() for v in value if str(v).strip()})
    if isinstance(value, str):
        return sorted({part.strip() for part in value.split(",") if part.strip()})
    return []


def _edge_confidence(edge: Dict[str, Any]) -> float:
    val = _as_float(edge.get("confidence"))
    if val is not None:
        return max(0.0, min(0.95, val))
    sources = set(_source_list(edge.get("inferred_from")))
    if "annotation" in sources:
        return 0.9
    if "service" in sources:
        return 0.8
    if "env_dns" in sources:
        return 0.75
    if "env_url" in sources:
        return 0.7
    if "env_host_port" in sources:
        return 0.65
    if "external_hostname" in sources:
        return 0.45
    return 0.5


def _edge_protocol(edge: Dict[str, Any]) -> str | None:
    protocol = edge.get("protocol")
    return str(protocol) if protocol else None


def _edge_port(edge: Dict[str, Any]) -> int | None:
    return _as_int(edge.get("port"))


def _append_unique(items: List[str], values: Iterable[str]) -> List[str]:
    seen = set(items)
    for value in values:
        if value and value not in seen:
            items.append(value)
            seen.add(value)
    return items


def _topology_confidence(edges: List[Dict[str, Any]]) -> str:
    if not edges:
        return "low"
    avg = sum(float(e.get("confidence") or 0.0) for e in edges) / len(edges)
    if avg >= 0.8:
        return "high"
    if avg >= 0.6:
        return "medium"
    return "low"


def build_topology_graph(
    services: List[Dict[str, Any]] | None,
    topology: Dict[str, Any] | None,
    data_quality: Dict[str, Any] | None = None,
    generated_at: str | None = None,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Project the canonical snapshot topology into a UI-ready graph."""
    topology = dict(topology or {})
    data_quality = dict(data_quality or {})
    services = services or []
    service_by_name = {str(s.get("name")): dict(s) for s in services if s.get("name")}
    details_by_name = {str(k): dict(v or {}) for k, v in (topology.get("service_details") or {}).items()}

    names = set(topology.get("services") or []) | set(service_by_name) | set(details_by_name)
    for edge in topology.get("edges") or []:
        if edge.get("from"):
            names.add(str(edge["from"]))
        if edge.get("to"):
            names.add(str(edge["to"]))

    nodes: List[Dict[str, Any]] = []
    node_id_by_service: Dict[str, str] = {}
    graph_data_sources = {"kubernetes"}

    for name in sorted(str(n) for n in names if str(n)):
        detail = _merge_detail(service_by_name.get(name, {}), details_by_name.get(name, {}))
        namespace = detail.get("namespace") or "unknown"
        node_id = workload_node_id(namespace, name)
        node_id_by_service[name] = node_id
        status, severity = _node_status(detail)
        log = _log_summary(detail)
        data_sources = ["kubernetes"]
        if log:
            data_sources.append("logs")
            graph_data_sources.add("logs")
        cpu_usage_cores = _as_float(detail.get("cpu_usage_cores"))
        memory_usage_bytes = _as_float(detail.get("memory_usage_bytes"))
        nodes.append(
            {
                "id": node_id,
                "name": name,
                "display_name": name,
                "kind": _infer_node_kind(name),
                "platform": "kubernetes",
                "namespace": namespace,
                "resource_name": name,
                "workload_kind": detail.get("workload_kind") or "Deployment",
                "status": status,
                "severity": severity,
                "replicas": _as_int(detail.get("replicas")),
                "available_replicas": _as_int(detail.get("available_replicas")),
                "unavailable_replicas": _as_int(detail.get("unavailable_replicas")),
                "restarts": _as_int(detail.get("restarts")) or 0,
                "cpu_utilization": _as_float(detail.get("cpu")),
                "memory_utilization": _as_float(detail.get("memory")),
                "cpu_usage_cores": cpu_usage_cores,
                "memory_usage_bytes": memory_usage_bytes,
                "request_count": _as_float(log.get("request_count")),
                "error_rate": _as_float(log.get("error_rate")),
                "request_latency_p95_ms": _as_float(log.get("request_latency_p95_ms")),
                "smell_count": 0,
                "recommendation_count": 0,
                "labels": detail.get("labels") if isinstance(detail.get("labels"), dict) else {},
                "data_sources": data_sources,
                "is_external": False,
            }
        )

    external_nodes: Dict[str, Dict[str, Any]] = {}
    graph_edges_by_id: Dict[str, Dict[str, Any]] = {}

    def ensure_external(name: str, edge_type: str) -> str:
        kind = _infer_node_kind(name, edge_type=edge_type, external=True)
        node_id = external_node_id(kind, name)
        if node_id not in external_nodes:
            external_nodes[node_id] = {
                "id": node_id,
                "name": name,
                "display_name": name,
                "kind": kind,
                "platform": "external",
                "namespace": None,
                "resource_name": name,
                "status": "unknown",
                "severity": "none",
                "labels": {},
                "data_sources": ["kubernetes"],
                "is_external": True,
            }
        return node_id

    def add_graph_edge(edge: Dict[str, Any], external_target: bool = False) -> None:
        src = str(edge.get("from") or "")
        tgt = str(edge.get("to") or "")
        typ = str(edge.get("type") or "unknown")
        if not src or not tgt:
            return
        from_node = node_id_by_service.get(src) or ensure_external(src, typ)
        to_node = node_id_by_service.get(tgt)
        if to_node is None or external_target:
            to_node = ensure_external(tgt, typ)
        edge_id = stable_edge_id(from_node, to_node, typ)
        sources = _source_list(edge.get("inferred_from"))
        evidence = [str(e) for e in (edge.get("evidence") or []) if str(e)]
        confidence = _edge_confidence(edge)
        existing = graph_edges_by_id.get(edge_id)
        if existing:
            existing["confidence"] = min(0.95, max(existing["confidence"], confidence) + 0.05)
            existing["inferred_from"] = _append_unique(existing["inferred_from"], sources)
            existing["evidence"] = _append_unique(existing["evidence"], evidence)
            return
        graph_edges_by_id[edge_id] = {
            "id": edge_id,
            "from": from_node,
            "to": to_node,
            "type": typ,
            "direction": "outbound",
            "status": "unknown",
            "confidence": confidence,
            "inferred_from": sources,
            "evidence": evidence,
            "protocol": _edge_protocol(edge),
            "port": _edge_port(edge),
            "data_sources": ["kubernetes"],
        }

    for edge in topology.get("edges") or []:
        add_graph_edge(dict(edge), external_target=False)
    for edge in topology.get("external_edges") or []:
        add_graph_edge(dict(edge), external_target=True)

    nodes.extend(external_nodes.values())
    graph_edges = sorted(graph_edges_by_id.values(), key=lambda e: e["id"])
    nodes = sorted(nodes, key=lambda n: n["id"])

    confidence = _topology_confidence(graph_edges)
    if data_quality.get("topology_confidence") and not graph_edges:
        confidence = str(data_quality["topology_confidence"])

    return {
        "nodes": nodes,
        "edges": graph_edges,
        "meta": {
            "run_id": run_id,
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "node_count": len(nodes),
            "edge_count": len(graph_edges),
            "topology_confidence": confidence,
            "data_sources": sorted(graph_data_sources),
        },
    }


def topology_graph_data_quality(graph: Dict[str, Any], missing_labels: int = 0) -> Dict[str, Any]:
    """Return graph-specific data quality counters for snapshot.data_quality."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    return {
        "topology_edges_inferred": len(edges),
        "topology_confidence": (graph.get("meta") or {}).get("topology_confidence") or "low",
        "topology_nodes_without_metrics": sum(
            1
            for n in nodes
            if not n.get("is_external") and n.get("cpu_usage_cores") is None and n.get("memory_usage_bytes") is None
        ),
        "topology_edges_low_confidence": sum(1 for e in edges if float(e.get("confidence") or 0.0) < 0.6),
        "topology_external_nodes": sum(1 for n in nodes if n.get("is_external")),
        "topology_missing_labels": int(missing_labels or 0),
    }
