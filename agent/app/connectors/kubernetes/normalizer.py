"""
Map collected Kubernetes state into the canonical snapshot shape (services,
aggregate signals, topology) before persistence.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from kubernetes.client import V1Deployment, V1Pod, V1Service

from agent.app.connectors.kubernetes.kube_labels import app_name_for_labels, app_name_for_pod
from agent.app.connectors.kubernetes.topology_builder import build_topology


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


def _deployment_app_replicas(deployments: List[V1Deployment]) -> Dict[str, int]:
    """Desired replicas keyed by logical app name (template labels)."""
    out: Dict[str, int] = defaultdict(int)
    for d in deployments:
        if not d.metadata or not d.spec or not d.spec.template or not d.spec.template.metadata:
            continue
        labels = d.spec.template.metadata.labels or {}
        app = app_name_for_labels(labels)
        if not app:
            continue
        r = d.spec.replicas
        if r is None:
            r = 0
        out[app] += int(r)
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
) -> Dict[str, Any]:
    """
    Produce ``services``, aggregate ``signals``, and ``topology`` dicts.

    ``signals`` uses cluster-level utilization (max across logical services) for
    MVP alignment with existing smell thresholds.
    """
    metrics_by_key = _index_metrics(pod_metrics)
    dep_replicas = _deployment_app_replicas(deployments)

    by_app: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"cpu_ratios": [], "mem_ratios": [], "restarts": 0, "pod_count": 0}
    )

    for pod in pods:
        app = app_name_for_pod(pod)
        if not app:
            continue
        slot = by_app[app]
        slot["pod_count"] += 1
        slot["restarts"] += _pod_restart_total(pod)

        ns = pod.metadata.namespace or ""
        name = pod.metadata.name or ""
        m = metrics_by_key.get((ns, name))
        use_cpu, use_mem = _pod_cpu_mem_usage(m) if m else (0.0, 0.0)
        cap_cpu, cap_mem = _pod_cpu_mem_capacity(pod)

        if m and cap_cpu > 0:
            slot["cpu_ratios"].append(min(1.5, use_cpu / cap_cpu))
        if m and cap_mem > 0:
            slot["mem_ratios"].append(min(1.5, use_mem / cap_mem))

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
        replicas = dep_replicas.get(app, int(slot["pod_count"]))
        services_out.append(
            {
                "name": app,
                "cpu": float(cpu_avg) if cpu_avg is not None else 0.0,
                "memory": float(mem_avg) if mem_avg is not None else 0.0,
                "replicas": replicas,
                "restarts": int(slot["restarts"]),
            }
        )

    app_names: Set[str] = set(by_app.keys())
    topology = build_topology(pods, services, app_names)

    queue_backlog: float | None = None
    for h in hpas:
        status = getattr(h, "status", None)
        if not status:
            continue
        metrics = getattr(status, "current_metrics", None) or []
        for cm in metrics:
            ext = getattr(cm, "external", None)
            if ext and getattr(ext, "current", None):
                cur = ext.current
                val = getattr(cur, "average_value", None) or getattr(cur, "value", None)
                if val is not None and (queue_backlog is None or float(val) > queue_backlog):
                    queue_backlog = float(val)

    signals: Dict[str, float] = {}
    if cpu_cluster:
        signals["cpu_utilization"] = max(cpu_cluster)
    if mem_cluster:
        signals["memory_utilization"] = max(mem_cluster)
    if queue_backlog is not None:
        signals["queue_backlog"] = queue_backlog

    return {"services": services_out, "signals": signals, "topology": topology}
