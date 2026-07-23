from kubernetes.client import (
    V1ConfigMap,
    V1ConfigMapEnvSource,
    V1Container,
    V1EnvVar,
    V1EnvFromSource,
    V1EnvVarSource,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1SecretEnvSource,
    V1SecretKeySelector,
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
    worker_edge = next(e for e in topology["edges"] if e["to"] == "worker")
    assert worker_edge["confidence"] >= 0.9
    assert "annotation" in worker_edge["inferred_from"]
    assert any("archagent.io/depends-on=queue:worker" == item for item in worker_edge["evidence"])


def test_build_topology_captures_meaningful_external_dependencies():
    pods = [
        _pod(
            "api-abc-123",
            "api",
            env=[V1EnvVar(name="PAYMENTS_URL", value="https://api.stripe.com/v1/charges")],
        )
    ]

    topology = build_topology(pods, [], {"api"})

    assert topology["edges"] == []
    assert topology["external_edges"] == [
        {
            "from": "api",
            "to": "api.stripe.com",
            "type": "http",
            "protocol": "https",
            "inferred_from": "external_hostname",
            "evidence": ["PAYMENTS_URL=<redacted>"],
            "confidence": 0.45,
        }
    ]


def test_build_topology_does_not_resolve_secret_key_refs():
    pods = [
        _pod(
            "api-abc-123",
            "api",
            env=[
                V1EnvVar(
                    name="DATABASE_URL",
                    value_from=V1EnvVarSource(
                        secret_key_ref=V1SecretKeySelector(name="api-secret", key="database-url")
                    ),
                )
            ],
        ),
        _pod("june-api-postgresql-abc-123", "june-api-postgresql"),
    ]

    topology = build_topology(pods, [], {"api", "june-api-postgresql"})

    assert topology["edges"] == []


def test_build_topology_resolves_env_from_config_maps_but_not_secrets():
    config_map = V1ConfigMap(
        metadata=V1ObjectMeta(name="api-config", namespace="default"),
        data={"QUEUE_URL": "jobs.default.svc"},
    )
    pods = [
        V1Pod(
            metadata=V1ObjectMeta(name="api-abc-123", namespace="default", labels={"app": "api"}),
            spec=V1PodSpec(
                containers=[
                    V1Container(
                        name="main",
                        env_from=[
                            V1EnvFromSource(config_map_ref=V1ConfigMapEnvSource(name="api-config")),
                            V1EnvFromSource(secret_ref=V1SecretEnvSource(name="api-secret")),
                        ],
                    )
                ]
            ),
        ),
        _pod("worker-abc-123", "worker"),
        _pod("redis-abc-123", "redis"),
    ]
    services = [_service("jobs", "worker")]

    topology = build_topology(
        pods,
        services,
        {"api", "worker", "redis"},
        config_maps=[config_map],
    )
    edges = {(e["from"], e["to"], e["type"]) for e in topology["edges"]}

    assert ("api", "worker", "queue") in edges
    assert ("api", "redis", "cache") not in edges
    assert any("QUEUE_URL from configmap/default/api-config/QUEUE_URL" in e["evidence"] for e in topology["edges"])


def test_build_topology_ignores_plain_config_flags_as_external_dependencies():
    config_map = V1ConfigMap(
        metadata=V1ObjectMeta(name="api-config", namespace="default"),
        data={
            "GIN_MODE": "debug",
            "DB_SSLMODE": "disable",
            "JUNE_SIM_PROFILE": "high-error-rate",
            "DB_HOST": "june-api-postgresql",
        },
    )
    pods = [
        V1Pod(
            metadata=V1ObjectMeta(name="api-abc-123", namespace="default", labels={"app": "api"}),
            spec=V1PodSpec(
                containers=[
                    V1Container(
                        name="main",
                        env_from=[V1EnvFromSource(config_map_ref=V1ConfigMapEnvSource(name="api-config"))],
                    )
                ]
            ),
        ),
        _pod("june-api-postgresql-abc-123", "june-api-postgresql"),
    ]

    topology = build_topology(
        pods,
        [],
        {"api", "june-api-postgresql"},
        config_maps=[config_map],
    )

    assert topology["external_edges"] == []
    assert [(e["from"], e["to"], e["type"]) for e in topology["edges"]] == [
        ("api", "june-api-postgresql", "db")
    ]
