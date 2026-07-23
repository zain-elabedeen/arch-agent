"""Manage connector snapshot tables with Alembic."""

from __future__ import annotations

from alembic import op

revision = "0008_connector_snapshot_schema"
down_revision = "0007_external_workos_invitations"
branch_labels = None
depends_on = None

CONNECTOR_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id UUID PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        snapshot JSONB,
        data_quality JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS service_metrics (
        run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        service_name TEXT NOT NULL,
        namespace TEXT,
        cpu DOUBLE PRECISION NOT NULL,
        memory DOUBLE PRECISION NOT NULL,
        cpu_usage_cores DOUBLE PRECISION,
        memory_usage_bytes DOUBLE PRECISION,
        replicas INTEGER NOT NULL,
        available_replicas INTEGER,
        unavailable_replicas INTEGER,
        restarts INTEGER NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, service_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
        cpu_utilization DOUBLE PRECISION,
        memory_utilization DOUBLE PRECISION,
        queue_backlog DOUBLE PRECISION,
        pod_restart_total DOUBLE PRECISION,
        unavailable_replicas DOUBLE PRECISION,
        single_instance_service_count DOUBLE PRECISION,
        hpa_scaling_pressure DOUBLE PRECISION,
        payload JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS topology (
        run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        type TEXT NOT NULL,
        inferred_from TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS log_events (
        run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        service_name TEXT NOT NULL,
        namespace TEXT,
        pod TEXT,
        level TEXT,
        category TEXT,
        status_code INTEGER,
        latency_ms DOUBLE PRECISION,
        is_error BOOLEAN NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        message_sample TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_topology_run_id ON topology (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_log_events_run_id ON log_events (run_id)",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS snapshot JSONB",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS data_quality JSONB",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS namespace TEXT",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS payload JSONB",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS pod_restart_total DOUBLE PRECISION",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS unavailable_replicas DOUBLE PRECISION",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS single_instance_service_count DOUBLE PRECISION",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS hpa_scaling_pressure DOUBLE PRECISION",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS cpu_usage_cores DOUBLE PRECISION",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS memory_usage_bytes DOUBLE PRECISION",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS available_replicas INTEGER",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS unavailable_replicas INTEGER",
    "ALTER TABLE topology ADD COLUMN IF NOT EXISTS inferred_from TEXT",
    "ALTER TABLE log_events ADD COLUMN IF NOT EXISTS pod TEXT",
    "ALTER TABLE log_events ADD COLUMN IF NOT EXISTS status_code INTEGER",
    "ALTER TABLE log_events ADD COLUMN IF NOT EXISTS latency_ms DOUBLE PRECISION",
    "ALTER TABLE log_events ADD COLUMN IF NOT EXISTS count INTEGER DEFAULT 1",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE topology ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE log_events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in CONNECTOR_SCHEMA_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS log_events")
    op.execute("DROP TABLE IF EXISTS topology")
    op.execute("DROP TABLE IF EXISTS signals")
    op.execute("DROP TABLE IF EXISTS service_metrics")
    op.execute("DROP TABLE IF EXISTS runs")
