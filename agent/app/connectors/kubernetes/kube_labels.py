"""Resolve logical workload names from Kubernetes labels."""

from __future__ import annotations

from typing import Dict

from kubernetes.client import V1Pod


def app_name_for_labels(labels: Dict[str, str]) -> str | None:
    if not labels:
        return None
    for key in ("app.kubernetes.io/name", "app", "k8s-app"):
        v = labels.get(key)
        if v:
            return str(v).lower()
    return None


def app_name_for_pod(pod: V1Pod) -> str | None:
    if not pod.metadata:
        return None
    name = app_name_for_labels(pod.metadata.labels or {})
    if name:
        return name
    if pod.metadata.name:
        base = pod.metadata.name.rsplit("-", 2)[0]
        return base.lower() if base else None
    return None
