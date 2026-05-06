from kubernetes.client import (
    V1Container,
    V1EnvVar,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1Service,
    V1ServiceSpec,
)

from agent.app.connectors.kubernetes.topology_builder import build_topology


def _pod(name: str, app: str, env: list[V1EnvVar] | None = None, annotations: dict | None = None) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(
            name=name,
            namespace="default",
            labels={"app": app},
            annotations=annotations or {},
        ),
        spec=V1PodSpec(containers=[V1Container(name="main", env=env or [])]),
    )


def _service(name: str, app: str) -> V1Service:
    return V1Service(
        metadata=V1ObjectMeta(name=name, namespace="default"),
        spec=V1ServiceSpec(selector={"app": app}),
    )


def test_build_topology_supports_short_dns_urls_and_dependency_annotations():
    pods = [
        _pod(
            "api-abc-123",
            "api",
            env=[
                V1EnvVar(name="DATABASE_URL", value="postgres://postgres:5432/app"),
                V1EnvVar(name="QUEUE_URL", value="jobs.default.svc"),
            ],
            annotations={"archagent.io/depends-on": "queue:worker"},
        ),
        _pod("postgres-abc-123", "postgres"),
        _pod("worker-abc-123", "worker"),
    ]
    services = [
        _service("postgres", "postgres"),
        _service("jobs", "worker"),
    ]

    topology = build_topology(pods, services, {"api", "postgres", "worker"})
    edges = {(e["from"], e["to"], e["type"]) for e in topology["edges"]}

    assert ("api", "postgres", "db") in edges
    assert ("api", "worker", "queue") in edges
    assert topology["services"] == ["api", "postgres", "worker"]
