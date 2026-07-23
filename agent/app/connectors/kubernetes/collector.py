"""
Fetch raw Kubernetes objects used by the normalizer and topology builder.

Metrics come from metrics-server (``metrics.k8s.io``); missing metrics-server is
non-fatal and yields an empty metrics map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from kubernetes.client import ApiException, V1ConfigMap, V1Deployment, V1Pod, V1Service

from agent.app.connectors.kubernetes.client import K8sApis, build_apis


@dataclass
class CollectedCluster:
    pods: List[V1Pod]
    deployments: List[V1Deployment]
    services: List[V1Service]
    config_maps: List[V1ConfigMap]
    pod_metrics: List[Dict[str, Any]]
    hpas: List[Any]


def _list_pod_metrics(apis: K8sApis) -> List[Dict[str, Any]]:
    last_exc: ApiException | None = None
    for version in ("v1", "v1beta1"):
        try:
            resp = apis.custom.list_cluster_custom_object(
                group="metrics.k8s.io",
                version=version,
                plural="pods",
            )
            return list[Dict[str, Any]](resp.get("items") or [])
        except ApiException as e:
            last_exc = e
            if e.status == 404:
                continue
            raise
    if last_exc and last_exc.status == 404:
        return []
    return []


def collect(apis: K8sApis | None = None) -> CollectedCluster:
    """Pull pods, deployments, services, pod metrics, and HPAs cluster-wide."""
    apis = apis or build_apis()
    pods = apis.core.list_pod_for_all_namespaces(watch=False).items
    deployments = apis.apps.list_deployment_for_all_namespaces(watch=False).items
    services = apis.core.list_service_for_all_namespaces(watch=False).items
    try:
        config_maps = apis.core.list_config_map_for_all_namespaces(watch=False).items
    except ApiException:
        config_maps = []
    metrics = _list_pod_metrics(apis)
    try:
        hpas = apis.autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces(watch=False).items
    except ApiException:
        hpas = []
    return CollectedCluster(
        pods=pods,
        deployments=deployments,
        services=services,
        config_maps=config_maps,
        pod_metrics=metrics,
        hpas=hpas,
    )


def unpack(collected: CollectedCluster) -> Tuple[Any, ...]:
    """Tuple form for ``normalize`` call sites matching the design doc."""
    return (
        collected.pods,
        collected.deployments,
        collected.services,
        collected.pod_metrics,
        collected.hpas,
        collected.config_maps,
    )
