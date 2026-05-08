from agent.app.connectors.repository import (
    _ddl_statements,
    log_events_t,
    runs_t,
    service_metrics_t,
    signals_t,
    topology_t,
)


def test_repository_tables_cover_normalized_snapshot_fields():
    assert {
        "id",
        "created_at",
        "snapshot",
        "data_quality",
    }.issubset(runs_t.c.keys())

    assert {
        "run_id",
        "service_name",
        "namespace",
        "cpu",
        "memory",
        "cpu_usage_cores",
        "memory_usage_bytes",
        "replicas",
        "available_replicas",
        "unavailable_replicas",
        "restarts",
    }.issubset(service_metrics_t.c.keys())

    assert {
        "run_id",
        "cpu_utilization",
        "memory_utilization",
        "queue_backlog",
        "pod_restart_total",
        "unavailable_replicas",
        "single_instance_service_count",
        "hpa_scaling_pressure",
        "payload",
    }.issubset(signals_t.c.keys())

    assert {
        "run_id",
        "source",
        "target",
        "type",
        "inferred_from",
    }.issubset(topology_t.c.keys())

    assert {
        "run_id",
        "service_name",
        "namespace",
        "pod",
        "level",
        "category",
        "status_code",
        "latency_ms",
        "is_error",
        "count",
        "message_sample",
    }.issubset(log_events_t.c.keys())


def test_repository_migrations_include_recent_normalizer_columns():
    ddl = "\n".join(_ddl_statements()).lower()

    for fragment in (
        "alter table runs add column if not exists data_quality jsonb",
        "alter table service_metrics add column if not exists namespace text",
        "alter table signals add column if not exists pod_restart_total double precision",
        "alter table signals add column if not exists unavailable_replicas double precision",
        "alter table signals add column if not exists single_instance_service_count double precision",
        "alter table signals add column if not exists hpa_scaling_pressure double precision",
        "alter table topology add column if not exists inferred_from text",
        "create table if not exists log_events",
        "create index if not exists idx_log_events_run_id on log_events",
    ):
        assert fragment in ddl
