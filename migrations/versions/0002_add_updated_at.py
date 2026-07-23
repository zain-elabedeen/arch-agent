"""Add updated timestamps to product control-plane tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_add_updated_at"
down_revision = "0001_product_control_plane"
branch_labels = None
depends_on = None

TABLE_NAMES = (
    "organizations",
    "users",
    "organization_memberships",
    "organization_invitations",
    "clusters",
    "cluster_namespaces",
    "collector_credentials",
    "cluster_heartbeats",
    "snapshot_runs",
    "workload_snapshots",
    "topology_edges",
    "analysis_runs",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_ingestion_jobs",
    "audit_events",
    "workos_event_receipts",
    "workos_sync_cursors",
)


def _has_updated_at(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name) and any(
        column["name"] == "updated_at" for column in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    for table_name in TABLE_NAMES:
        if _has_updated_at(table_name):
            continue
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text(f'UPDATE "{table_name}" SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL'))
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "updated_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )


def downgrade() -> None:
    for table_name in reversed(TABLE_NAMES):
        if not _has_updated_at(table_name):
            continue
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("updated_at")
