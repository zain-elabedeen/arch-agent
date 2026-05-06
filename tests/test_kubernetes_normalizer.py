from types import SimpleNamespace

from kubernetes.client import (
    V1Container,
    V1ContainerStatus,
    V1Deployment,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1PodStatus,
    V1PodTemplateSpec,
    V1ResourceRequirements,
)

from agent.app.connectors.kubernetes.normalizer import normalize


def _pod(name: str, app: str, restarts: int = 0, namespace: str = "default") -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(name=name, namespace=namespace, labels={"app": app}),
        spec=V1PodSpec(
            containers=[
                V1Container(
                    name="main",
                    resources=V1ResourceRequirements(
                        limits={"cpu": "1", "memory": "1Gi"},
                        requests={"cpu": "500m", "memory": "512Mi"},
                    ),
                )
            ]
        ),
        status=V1PodStatus(
            container_statuses=[
                V1ContainerStatus(
                    name="main",
                    image="app:latest",
                    image_id="docker://abc",
                    ready=True,
                    restart_count=restarts,
                )
            ]
        ),
    )


def _deployment(app: str, replicas: int, available: int, namespace: str = "default") -> V1Deployment:
    return V1Deployment(
        metadata=V1ObjectMeta(name=app, namespace=namespace),
        spec=V1DeploymentSpec(
            replicas=replicas,
            selector={"matchLabels": {"app": app}},
            template=V1PodTemplateSpec(metadata=V1ObjectMeta(labels={"app": app})),
        ),
        status=V1DeploymentStatus(
            replicas=replicas,
            available_replicas=available,
            unavailable_replicas=replicas - available,
        ),
    )


def test_normalize_emits_kubernetes_native_signals_and_data_quality():
    pods = [_pod("api-abc-123", "api", restarts=4)]
    pod_metrics = [
        {
            "metadata": {"namespace": "default", "name": "api-abc-123"},
            "containers": [{"usage": {"cpu": "950m", "memory": "950Mi"}}],
        }
    ]
    hpa = SimpleNamespace(
        status=SimpleNamespace(
            current_replicas=2,
            desired_replicas=4,
            current_metrics=[
                SimpleNamespace(
                    external=SimpleNamespace(current=SimpleNamespace(value="12000"))
                )
            ],
        )
    )

    out = normalize(
        pods=pods,
        deployments=[_deployment("api", replicas=3, available=2)],
        services=[],
        pod_metrics=pod_metrics,
        hpas=[hpa],
    )

    assert out["signals"]["cpu_utilization"] == 0.95
    assert out["signals"]["memory_utilization"] > 0.9
    assert out["signals"]["queue_backlog"] == 12000
    assert out["signals"]["pod_restart_total"] == 4
    assert out["signals"]["unavailable_replicas"] == 1
    assert "single_instance_service_count" not in out["signals"]
    assert out["signals"]["hpa_scaling_pressure"] == 2.0
    assert out["services"][0]["available_replicas"] == 2
    assert out["services"][0]["cpu_usage_cores"] == 0.95
    assert out["data_quality"]["metrics_server_available"] is True
    assert out["data_quality"]["services_with_metrics"] == 1


def test_normalize_excludes_kubernetes_system_namespaces_by_default():
    out = normalize(
        pods=[
            _pod("kube-dns-abc-123", "kube-dns", restarts=20, namespace="kube-system"),
            _pod("api-abc-123", "api", restarts=0, namespace="default"),
        ],
        deployments=[
            _deployment("kube-dns", replicas=1, available=1, namespace="kube-system"),
            _deployment("api", replicas=2, available=2, namespace="default"),
        ],
        services=[],
        pod_metrics=[],
        hpas=[],
    )

    assert [svc["name"] for svc in out["services"]] == ["api"]
    assert "pod_restart_total" not in out["signals"]
    assert out["data_quality"]["pods_excluded_by_namespace"] == 1
    assert "kube-system" in out["data_quality"]["excluded_namespaces"]


def test_normalize_include_namespaces_limits_collection_scope():
    out = normalize(
        pods=[
            _pod("api-abc-123", "api", namespace="default"),
            _pod("worker-abc-123", "worker", namespace="demo"),
        ],
        deployments=[
            _deployment("api", replicas=1, available=1, namespace="default"),
            _deployment("worker", replicas=1, available=1, namespace="demo"),
        ],
        services=[],
        pod_metrics=[],
        hpas=[],
        include_namespaces={"demo"},
        exclude_namespaces=set(),
    )

    assert [svc["name"] for svc in out["services"]] == ["worker"]
