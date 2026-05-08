"""
Kubernetes-backed implementation of the source-neutral logs connector.

This module knows how to read pod logs. Everything after raw line collection is
handled by the generic ``agent.app.connectors.logs`` pipeline so additional log
sources can be added without changing normalization or smell detection.
"""

from __future__ import annotations

from typing import Iterable, List, Set

from kubernetes.client import ApiException, V1Pod

from agent.app.connectors.kubernetes.client import K8sApis
from agent.app.connectors.kubernetes.kube_labels import app_name_for_pod
from agent.app.connectors.logs.models import RawLogBatch
from agent.app.logging_utils import get_logger

logger = get_logger("agent.connectors.logs.kubernetes")


def _namespace_allowed(ns: str, include: Set[str], exclude: Set[str]) -> bool:
    if include and ns not in include:
        return False
    return ns not in exclude


def _pod_containers(pod: V1Pod) -> List[str | None]:
    if not pod.spec or not pod.spec.containers:
        return [None]
    return [str(c.name) for c in pod.spec.containers if c.name] or [None]


def collect_kubernetes_logs(
    apis: K8sApis,
    pods: Iterable[V1Pod],
    *,
    include_namespaces: Set[str],
    exclude_namespaces: Set[str],
    since_seconds: int,
    tail_lines: int,
) -> List[RawLogBatch]:
    """
    Read recent pod logs and return source-neutral raw batches.

    Kubernetes timestamps are requested so the generic normalizer can preserve
    event time for both JSON and plain-text logs.
    """
    out: List[RawLogBatch] = []
    since = max(1, int(since_seconds))
    tail = max(1, int(tail_lines))

    for pod in pods:
        if not pod.metadata or not pod.metadata.name:
            continue
        namespace = str(pod.metadata.namespace or "default")
        if not _namespace_allowed(namespace, include_namespaces, exclude_namespaces):
            continue
        service = app_name_for_pod(pod)
        if not service:
            continue

        for container in _pod_containers(pod):
            try:
                raw = apis.core.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=namespace,
                    container=container,
                    since_seconds=since,
                    tail_lines=tail,
                    timestamps=True,
                )
                lines = [line for line in str(raw or "").splitlines() if line.strip()]
                out.append(
                    RawLogBatch(
                        source="kubernetes",
                        service=service,
                        namespace=namespace,
                        resource=pod.metadata.name,
                        container=container,
                        lines=lines,
                    )
                )
            except ApiException as e:
                logger.warning(
                    "pod log read failed namespace=%s pod=%s container=%s status=%s reason=%s",
                    namespace,
                    pod.metadata.name,
                    container,
                    e.status,
                    e.reason,
                )
                out.append(
                    RawLogBatch(
                        source="kubernetes",
                        service=service,
                        namespace=namespace,
                        resource=pod.metadata.name,
                        container=container,
                        lines=[],
                        read_error=f"{e.status}:{e.reason}",
                    )
                )
            except Exception as e:
                logger.warning(
                    "pod log read failed namespace=%s pod=%s container=%s error=%s",
                    namespace,
                    pod.metadata.name,
                    container,
                    e,
                )
                out.append(
                    RawLogBatch(
                        source="kubernetes",
                        service=service,
                        namespace=namespace,
                        resource=pod.metadata.name,
                        container=container,
                        lines=[],
                        read_error=str(e),
                    )
                )
    return out
