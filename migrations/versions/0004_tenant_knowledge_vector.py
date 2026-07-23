"""Enable pgvector for tenant-scoped uploaded knowledge chunks."""

from __future__ import annotations

from alembic import op

revision = "0004_tenant_knowledge_vector"
down_revision = "0003_hosted_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_scope "
        "ON knowledge_chunks (organization_id, scope)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding "
        "ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_knowledge_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_chunks_tenant_scope")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding")
