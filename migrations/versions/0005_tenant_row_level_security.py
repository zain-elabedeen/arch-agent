"""Apply PostgreSQL row-level security to tenant data tables."""

from __future__ import annotations

from alembic import op

revision = "0005_tenant_row_level_security"
down_revision = "0004_tenant_knowledge_vector"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "snapshot_runs",
    "workload_snapshots",
    "topology_edges",
    "analysis_runs",
    "audit_events",
)

TENANT_POLICY = """
    current_setting('archagent.internal', true) = 'true'
    OR organization_id::text = NULLIF(current_setting('archagent.organization_id', true), '')
"""

KNOWLEDGE_READ_POLICY = """
    current_setting('archagent.internal', true) = 'true'
    OR scope = 'global'
    OR organization_id::text = NULLIF(current_setting('archagent.organization_id', true), '')
"""

KNOWLEDGE_WRITE_POLICY = """
    current_setting('archagent.internal', true) = 'true'
    OR (
        scope = 'organization'
        AND organization_id::text = NULLIF(current_setting('archagent.organization_id', true), '')
    )
"""


def _enable(table: str, using: str, check: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS archagent_tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY archagent_tenant_isolation ON {table} "
        f"USING ({using}) WITH CHECK ({check})"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        _enable(table, TENANT_POLICY, TENANT_POLICY)
    for table in ("knowledge_documents", "knowledge_chunks"):
        _enable(table, KNOWLEDGE_READ_POLICY, KNOWLEDGE_WRITE_POLICY)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in (*TENANT_TABLES, "knowledge_documents", "knowledge_chunks"):
        op.execute(f"DROP POLICY IF EXISTS archagent_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
