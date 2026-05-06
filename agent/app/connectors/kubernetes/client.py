"""
Thin factory around the official Kubernetes Python client.

Loads in-cluster config first (when ``KUBERNETES_SERVICE_HOST`` is set), otherwise
``~/.kube/config`` for local development.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from kubernetes import client, config
from kubernetes.client import ApiClient

from agent.app.logging_utils import get_logger

logger = get_logger("agent.connectors.k8s.client")


@dataclass(frozen=True)
class K8sApis:
    """Bundled API clients used by the collector."""

    core: client.CoreV1Api
    apps: client.AppsV1Api
    custom: client.CustomObjectsApi
    autoscaling: client.AutoscalingV2Api
    host: str
    auth_source: str


def _host_is_loopback(host: str) -> bool:
    parsed = urlparse(host)
    return parsed.hostname in {"127.0.0.1", "localhost"}


def build_apis() -> K8sApis:
    """Construct API clients; raises if no kubeconfig / in-cluster credentials."""
    auth_source = "incluster"
    try:
        config.load_incluster_config()
    except config.ConfigException:
        auth_source = "kubeconfig"
        config.load_kube_config()

    api_client = ApiClient()
    host = str(api_client.configuration.host or "")
    if host:
        logger.info("k8s client initialized auth_source=%s host=%s", auth_source, host)
    if auth_source == "kubeconfig" and _host_is_loopback(host):
        logger.warning(
            "k8s kubeconfig resolves to loopback host=%s. In containers this often fails unless that host/port "
            "is reachable from the container. Prefer a real cluster IP/hostname in kubeconfig.",
            host,
        )

    return K8sApis(
        core=client.CoreV1Api(api_client),
        apps=client.AppsV1Api(api_client),
        custom=client.CustomObjectsApi(api_client),
        autoscaling=client.AutoscalingV2Api(api_client),
        host=host,
        auth_source=auth_source,
    )
