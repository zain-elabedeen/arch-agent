"""Manage global architecture knowledge pgvector tables with Alembic."""

from __future__ import annotations

from alembic import op

revision = "0009_arch_knowledge"
down_revision = "0008_connector_snapshot_schema"
branch_labels = None
depends_on = None

ARCHITECTURE_KNOWLEDGE_DIMENSIONS = 1536

ARCHITECTURE_KNOWLEDGE_SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS architecture_knowledge_sources (
        id UUID PRIMARY KEY,
        title TEXT NOT NULL,
        source_type TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS architecture_knowledge_chunks (
        id UUID PRIMARY KEY,
        source_id UUID NOT NULL REFERENCES architecture_knowledge_sources(id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL UNIQUE,
        embedding vector({ARCHITECTURE_KNOWLEDGE_DIMENSIONS}) NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source_id, chunk_index)
    )
    """,
    (
        "ALTER TABLE architecture_knowledge_sources "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ),
    (
        "ALTER TABLE architecture_knowledge_chunks "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_arch_knowledge_chunks_source_id "
        "ON architecture_knowledge_chunks (source_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_arch_knowledge_chunks_embedding "
        "ON architecture_knowledge_chunks USING ivfflat (embedding vector_cosine_ops)"
    ),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in ARCHITECTURE_KNOWLEDGE_SCHEMA_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_arch_knowledge_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS idx_arch_knowledge_chunks_source_id")
    op.execute("DROP TABLE IF EXISTS architecture_knowledge_chunks")
    op.execute("DROP TABLE IF EXISTS architecture_knowledge_sources")
