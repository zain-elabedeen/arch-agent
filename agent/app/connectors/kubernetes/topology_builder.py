"""
Infer a minimal service graph from pods, Services, and optional env references.

MVP: nodes come from the ``app`` / ``app.kubernetes.io/name`` pod grouping.
Edges are added when pod env values reference another workload via Kubernetes
DNS (``*.svc.cluster.local``) and the target resolves to a known app name.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set

from kubernetes.client import V1Pod, V1Service

from agent.app.connectors.kubernetes.kube_labels import app_name_for_labels, app_name_for_pod

_SVC_DNS = re.compile(
    r"([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.(?:[a-z0-9-]+\.)*svc\.cluster\.local",
    re.IGNORECASE,
)


def _service_selectors(svc: V1Service) -> Dict[str, str]:
    spec = svc.spec
    if not spec or not spec.selector:
        return {}
    return dict(spec.selector)


def _selector_matches(labels: Dict[str, str], selector: Dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def _target_app_for_hostname(
    hostname: str,
    app_names: Set[str],
    services: List[V1Service],
    pod_labels_by_ns: Dict[tuple[str, str], Dict[str, str]],
) -> str | None:
    h = hostname.lower().strip()
    if h in app_names:
        return h
    for svc in services:
        if not svc.metadata or not svc.metadata.name:
            continue
        if svc.metadata.name.lower() != h:
            continue
        sel = _service_selectors(svc)
        if not sel:
            continue
        ns = svc.metadata.namespace or ""
        for (pns, _), labels in pod_labels_by_ns.items():
            if pns != ns:
                continue
            if _selector_matches(labels, sel):
                cand = app_name_for_labels(labels)
                if cand:
                    return cand
    return None


def _edge_type_for_target(target_app: str) -> str:
    t = target_app.lower()
    if any(x in t for x in ("postgres", "mysql", "mongo", "redis", "elastic", "cockroach", "clickhouse", "mariadb")):
        return "db"
    if any(x in t for x in ("kafka", "rabbit", "nats", "sqs", "queue", "worker")):
        return "queue"
    return "http"


def _env_strings(pod: V1Pod) -> Iterable[str]:
    if not pod.spec:
        return
    for c in pod.spec.containers or []:
        for e in c.env or []:
            if e.value:
                yield e.value
        if c.env_from:
            for _ in c.env_from or []:
                continue


def build_topology(
    pods: List[V1Pod],
    services: List[V1Service],
    app_names: Set[str],
) -> Dict[str, Any]:
    """
    Return ``{"services": [...], "edges": [{"from", "to", "type"}, ...]}``
    using alias keys ``from`` / ``to`` for ``ServiceTopology``.
    """
    pod_labels_by_ns: Dict[tuple[str, str], Dict[str, str]] = {}
    for pod in pods:
        if not pod.metadata or not pod.metadata.name:
            continue
        ns = pod.metadata.namespace or ""
        labels = pod.metadata.labels or {}
        pod_labels_by_ns[(ns, pod.metadata.name)] = labels

    services_sorted = sorted(app_names)
    edges: List[Dict[str, str]] = []
    seen: Set[tuple[str, str, str]] = set()

    for pod in pods:
        src = app_name_for_pod(pod)
        if not src:
            continue
        for val in _env_strings(pod):
            for m in _SVC_DNS.finditer(val):
                host = m.group(1).lower()
                tgt = _target_app_for_hostname(host, app_names, services, pod_labels_by_ns)
                if not tgt or tgt == src:
                    continue
                typ = _edge_type_for_target(tgt)
                key = (src, tgt, typ)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": src, "to": tgt, "type": typ})

    return {"services": services_sorted, "edges": edges}
