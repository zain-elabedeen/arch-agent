"""
Map collected Kubernetes state into the canonical snapshot shape (services,
aggregate signals, topology) before persistence.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

from kubernetes.client import V1Deployment, V1Pod, V1Service

from agent.app.connectors.kubernetes.kube_labels import app_name_for_labels, app_name_for_pod
from agent.app.connectors.kubernetes.topology_builder import build_topology

DEFAULT_EXCLUDED_NAMESPACES = frozenset(
    {"kube-system", "kube-public", "kube-node-lease", "kubernetes-dashboard"}
)


def _namespace_set(values: Iterable[str] | None) -> Set[str]:
    return {str(v).strip() for v in values or [] if str(v).strip()}


def _object_namespace(obj: Any) -> str:
    metadata = getattr(obj, "metadata", None)
    return str(getattr(metadata, "namespace", "") or "")


def _metric_namespace(item: Dict[str, Any]) -> str:
    return str((item.get("metadata") or {}).get("namespace") or "")


def _namespace_allowed(ns: str, include: Set[str], exclude: Set[str]) -> bool:
    if include and ns not in include:
        return False
    return ns not in exclude


def _filter_by_namespace(items: List[Any], include: Set[str], exclude: Set[str]) -> Tuple[List[Any], int]:
    kept: List[Any] = []
    skipped = 0
    for item in items:
        if _namespace_allowed(_object_namespace(item), include, exclude):
            kept.append(item)
        else:
            skipped += 1
    return kept, skipped


def _parse_cpu_to_cores(s: str) -> float:
    s = str(s).strip()
    if not s:
        return 0.0
    if s.endswith("n"):
        return float(s[:-1]) / 1e9
    if s.endswith("u"):
        return float(s[:-1]) / 1e6
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    return float(s)


def _parse_memory_to_bytes(s: str) -> float:
    s = str(s).strip()
    if not s:
        return 0.0
    for suf, mul in (
        ("Ki", 1024.0),
        ("Mi", 1024.0**2),
        ("Gi", 1024.0**3),
        ("Ti", 1024.0**4),
        ("K", 1000.0),
        ("M", 1000.0**2),
        ("G", 1000.0**3),
        ("T", 1000.0**4),
    ):
        if s.endswith(suf):
            return float(s[: -len(suf)]) * mul
    return float(s)


def _parse_quantity_to_float(value: Any) -> float:
    """Best-effort parser for Kubernetes DecimalSI/BinarySI quantity values."""
    s = str(value).strip()
    if not s:
        return 0.0
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    for suf, mul in (
        ("Ki", 1024.0),
        ("Mi", 1024.0**2),
        ("Gi", 1024.0**3),
        ("Ti", 1024.0**4),
        ("K", 1000.0),
        ("M", 1000.0**2),
        ("G", 1000.0**3),
        ("T", 1000.0**4),
    ):
        if s.endswith(suf):
            return float(s[: -len(suf)]) * mul
    return float(s)


def _pod_cpu_mem_usage(pod_metric: Dict[str, Any]) -> Tuple[float, float]:
    cpu = 0.0
    mem = 0.0
    for c in pod_metric.get("containers") or []:
        usage = c.get("usage") or {}
        if usage.get("cpu"):
            cpu += _parse_cpu_to_cores(usage["cpu"])
        if usage.get("memory"):
            mem += _parse_memory_to_bytes(usage["memory"])
    return cpu, mem


def _pod_cpu_mem_capacity(pod: V1Pod) -> Tuple[float, float]:
    """Sum limits (else requests) for all containers — used as utilization denominator."""
    cpu_lim = 0.0
    mem_lim = 0.0
    cpu_req = 0.0
    mem_req = 0.0
    if not pod.spec:
        return 0.0, 0.0
    for c in pod.spec.containers or []:
        res = c.resources
        if not res:
            continue
        lim = res.limits or {}
        req = res.requests or {}
        if lim.get("cpu"):
            cpu_lim += _parse_cpu_to_cores(str(lim["cpu"]))
        if lim.get("memory"):
            mem_lim += _parse_memory_to_bytes(str(lim["memory"]))
        if req.get("cpu"):
            cpu_req += _parse_cpu_to_cores(str(req["cpu"]))
        if req.get("memory"):
            mem_req += _parse_memory_to_bytes(str(req["memory"]))
    if cpu_lim > 0 or mem_lim > 0:
        return cpu_lim, mem_lim
    return cpu_req, mem_req


def _index_metrics(items: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in items:
        meta = item.get("metadata") or {}
        name = meta.get("name")
        ns = meta.get("namespace")
        if name and ns:
            out[(ns, name)] = item
    return out


def _deployment_app_status(deployments: List[V1Deployment]) -> Dict[str, Dict[str, int]]:
    """Desired/available/unavailable replicas keyed by logical app name."""
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {"replicas": 0, "available_replicas": 0, "unavailable_replicas": 0})
    for d in deployments:
        if not d.metadata or not d.spec or not d.spec.template or not d.spec.template.metadata:
            continue
        labels = d.spec.template.metadata.labels or {}
        app = app_name_for_labels(labels)
        if not app:
            continue
        desired = int(d.spec.replicas or 0)
        status = d.status
        available = int(getattr(status, "available_replicas", None) or 0)
        unavailable = getattr(status, "unavailable_replicas", None)
        if unavailable is None:
            unavailable = max(0, desired - available)
        slot = out[app]
        slot["replicas"] += desired
        slot["available_replicas"] += available
        slot["unavailable_replicas"] += int(unavailable or 0)
    return dict(out)


def _pod_restart_total(pod: V1Pod) -> int:
    total = 0
    if not pod.status:
        return 0
    for cs in pod.status.container_statuses or []:
        total += int(cs.restart_count or 0)
    return total


def normalize(
    pods: List[V1Pod],
    deployments: List[V1Deployment],
    services: List[V1Service],
    pod_metrics: List[Dict[str, Any]],
    hpas: List[Any],
    include_namespaces: Iterable[str] | None = None,
    exclude_namespaces: Iterable[str] | None = DEFAULT_EXCLUDED_NAMESPACES,
) -> Dict[str, Any]:
    """
    Produce ``services``, aggregate ``signals``, and ``topology`` dicts.

    ``signals`` uses cluster-level utilization (max across logical services) for
    MVP alignment with existing smell thresholds.
    """
    include_ns = _namespace_set(include_namespaces)
    exclude_ns = _namespace_set(exclude_namespaces)
    pods, pods_excluded = _filter_by_namespace(pods, include_ns, exclude_ns)
    deployments, _ = _filter_by_namespace(deployments, include_ns, exclude_ns)
    services, _ = _filter_by_namespace(services, include_ns, exclude_ns)
    hpas, _ = _filter_by_namespace(hpas, include_ns, exclude_ns)
    pod_metrics = [
        m for m in pod_metrics if _namespace_allowed(_metric_namespace(m), include_ns, exclude_ns)
    ]

    metrics_by_key = _index_metrics(pod_metrics)
    dep_status = _deployment_app_status(deployments)

    by_app: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "cpu_ratios": [],
            "mem_ratios": [],
            "cpu_usage": [],
            "mem_usage": [],
            "restarts": 0,
            "pod_count": 0,
            "namespaces": set(),
        }
    )
    pods_without_app_label = 0

    for pod in pods:
        labels = pod.metadata.labels if pod.metadata else {}
        if not app_name_for_labels(labels or {}):
            pods_without_app_label += 1
        app = app_name_for_pod(pod)
        if not app:
            continue
        slot = by_app[app]
        slot["pod_count"] += 1
        slot["restarts"] += _pod_restart_total(pod)
        if pod.metadata and pod.metadata.namespace:
            slot["namespaces"].add(pod.metadata.namespace)

        ns = pod.metadata.namespace or ""
        name = pod.metadata.name or ""
        m = metrics_by_key.get((ns, name))
        use_cpu, use_mem = _pod_cpu_mem_usage(m) if m else (0.0, 0.0)
        cap_cpu, cap_mem = _pod_cpu_mem_capacity(pod)

        if m and cap_cpu > 0:
            slot["cpu_ratios"].append(min(1.5, use_cpu / cap_cpu))
        if m and cap_mem > 0:
            slot["mem_ratios"].append(min(1.5, use_mem / cap_mem))
        if m:
            slot["cpu_usage"].append(use_cpu)
            slot["mem_usage"].append(use_mem)

    services_out: List[Dict[str, Any]] = []
    cpu_cluster: List[float] = []
    mem_cluster: List[float] = []

    for app in sorted(by_app.keys()):
        slot = by_app[app]
        cpu_avg = sum(slot["cpu_ratios"]) / len(slot["cpu_ratios"]) if slot["cpu_ratios"] else None
        mem_avg = sum(slot["mem_ratios"]) / len(slot["mem_ratios"]) if slot["mem_ratios"] else None
        if cpu_avg is not None:
            cpu_cluster.append(cpu_avg)
        if mem_avg is not None:
            mem_cluster.append(mem_avg)
        dep = dep_status.get(app, {})
        replicas = dep.get("replicas", int(slot["pod_count"]))
        available = dep.get("available_replicas")
        unavailable = dep.get("unavailable_replicas")
        services_out.append(
            {
                "name": app,
                "namespace": sorted(slot["namespaces"])[0] if len(slot["namespaces"]) == 1 else None,
                "cpu": float(cpu_avg) if cpu_avg is not None else 0.0,
                "memory": float(mem_avg) if mem_avg is not None else 0.0,
                "cpu_usage_cores": float(sum(slot["cpu_usage"])) if slot["cpu_usage"] else None,
                "memory_usage_bytes": float(sum(slot["mem_usage"])) if slot["mem_usage"] else None,
                "replicas": replicas,
                "available_replicas": int(available) if available is not None else None,
                "unavailable_replicas": int(unavailable) if unavailable is not None else None,
                "restarts": int(slot["restarts"]),
            }
        )

    app_names: Set[str] = set(by_app.keys())
    topology = build_topology(pods, services, app_names)

    queue_backlog: float | None = None
    hpa_current_replicas = 0
    hpa_desired_replicas = 0
    for h in hpas:
        hpa_current_replicas += int(getattr(getattr(h, "status", None), "current_replicas", None) or 0)
        hpa_desired_replicas += int(getattr(getattr(h, "status", None), "desired_replicas", None) or 0)
        status = getattr(h, "status", None)
        if not status:
            continue
        metrics = getattr(status, "current_metrics", None) or []
        for cm in metrics:
            ext = getattr(cm, "external", None)
            if ext and getattr(ext, "current", None):
                cur = ext.current
                val = getattr(cur, "average_value", None) or getattr(cur, "value", None)
                parsed = _parse_quantity_to_float(val) if val is not None else None
                if parsed is not None and (queue_backlog is None or parsed > queue_backlog):
                    queue_backlog = parsed

    signals: Dict[str, float] = {}
    if cpu_cluster:
        signals["cpu_utilization"] = max(cpu_cluster)
    if mem_cluster:
        signals["memory_utilization"] = max(mem_cluster)
    if queue_backlog is not None:
        signals["queue_backlog"] = queue_backlog
    total_restarts = sum(int(s["restarts"]) for s in services_out)
    total_unavailable = sum(int(s.get("unavailable_replicas") or 0) for s in services_out)
    single_instance_count = sum(1 for s in services_out if int(s.get("replicas") or 0) <= 1)
    if total_restarts:
        signals["pod_restart_total"] = float(total_restarts)
    if total_unavailable:
        signals["unavailable_replicas"] = float(total_unavailable)
    if single_instance_count:
        signals["single_instance_service_count"] = float(single_instance_count)
    if hpa_desired_replicas and hpa_current_replicas:
        signals["hpa_scaling_pressure"] = hpa_desired_replicas / max(1.0, float(hpa_current_replicas))

    services_with_metrics = sum(1 for s in services_out if s.get("cpu_usage_cores") is not None or s.get("memory_usage_bytes") is not None)
    edge_count = len(topology.get("edges") or [])
    data_quality = {
        "metrics_server_available": bool(pod_metrics),
        "services_with_metrics": services_with_metrics,
        "services_without_metrics": max(0, len(services_out) - services_with_metrics),
        "excluded_namespaces": sorted(exclude_ns),
        "pods_excluded_by_namespace": pods_excluded,
        "pods_without_app_label": pods_without_app_label,
        "topology_edges_inferred": edge_count,
        "topology_confidence": "medium" if edge_count else "low",
    }

    return {"services": services_out, "signals": signals, "topology": topology, "data_quality": data_quality}
