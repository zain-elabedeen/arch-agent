"""Postgres/pgvector repository for architecture knowledge chunks."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import Engine, create_engine, text

from agent.app.config import Settings
from agent.app.knowledge.embeddings import vector_literal
from agent.app.knowledge.models import KnowledgeChunk, KnowledgeChunkReference

_SCHEMA_READY: set[str] = set()


class KnowledgeRepository(Protocol):
    """Storage operations needed by ingestion and retrieval."""

    def ensure_schema(self) -> None: ...

    def upsert_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> tuple[int, int]: ...

    def search(self, embedding: list[float], *, top_k: int) -> list[KnowledgeChunkReference]: ...


class PostgresKnowledgeRepository:
    """Postgres repository using pgvector for nearest-neighbor search."""

    def __init__(self, engine: Engine, *, dimensions: int):
        self.engine = engine
        self.dimensions = dimensions

    def ensure_schema(self) -> None:
        if self.engine.dialect.name != "postgresql":
            raise RuntimeError(f"Knowledge repository requires PostgreSQL; got dialect={self.engine.dialect.name}")
        key = f"{self.engine.url}:{self.dimensions}"
        if key in _SCHEMA_READY:
            return
        with self.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS architecture_knowledge_sources (
                        id UUID PRIMARY KEY,
                        title TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        path TEXT NOT NULL UNIQUE,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS architecture_knowledge_chunks (
                        id UUID PRIMARY KEY,
                        source_id UUID NOT NULL REFERENCES architecture_knowledge_sources(id) ON DELETE CASCADE,
                        chunk_index INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        content_hash TEXT NOT NULL UNIQUE,
                        embedding vector({self.dimensions}) NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (source_id, chunk_index)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_arch_knowledge_chunks_source_id "
                    "ON architecture_knowledge_chunks (source_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_arch_knowledge_chunks_embedding "
                    "ON architecture_knowledge_chunks USING ivfflat (embedding vector_cosine_ops)"
                )
            )
        _SCHEMA_READY.add(key)

    def upsert_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> tuple[int, int]:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        self.ensure_schema()
        indexed = 0
        skipped = 0
        with self.engine.begin() as conn:
            for chunk, embedding in zip(chunks, embeddings):
                existing = conn.execute(
                    text("SELECT id FROM architecture_knowledge_chunks WHERE content_hash = :content_hash"),
                    {"content_hash": chunk.content_hash},
                ).scalar_one_or_none()
                if existing:
                    skipped += 1
                    continue

                source_id = conn.execute(
                    text(
                        """
                        INSERT INTO architecture_knowledge_sources (id, title, source_type, path, metadata)
                        VALUES (:id, :title, :source_type, :path, CAST(:metadata AS jsonb))
                        ON CONFLICT (path) DO UPDATE
                        SET title = EXCLUDED.title,
                            source_type = EXCLUDED.source_type,
                            metadata = EXCLUDED.metadata
                        RETURNING id
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "title": chunk.source_title,
                        "source_type": chunk.source_type,
                        "path": chunk.path,
                        "metadata": _json_dumps(chunk.metadata),
                    },
                ).scalar_one()

                conn.execute(
                    text(
                        """
                        INSERT INTO architecture_knowledge_chunks (
                            id, source_id, chunk_index, content, content_hash, embedding, metadata
                        )
                        VALUES (
                            :id, :source_id, :chunk_index, :content, :content_hash,
                            CAST(:embedding AS vector), CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (source_id, chunk_index) DO UPDATE
                        SET content = EXCLUDED.content,
                            content_hash = EXCLUDED.content_hash,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "source_id": source_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "content_hash": chunk.content_hash,
                        "embedding": vector_literal(embedding),
                        "metadata": _json_dumps(chunk.metadata),
                    },
                )
                indexed += 1
        return indexed, skipped

    def search(self, embedding: list[float], *, top_k: int) -> list[KnowledgeChunkReference]:
        self.ensure_schema()
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        s.title,
                        s.source_type,
                        s.path,
                        c.content,
                        c.metadata,
                        1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
                    FROM architecture_knowledge_chunks c
                    JOIN architecture_knowledge_sources s ON s.id = c.source_id
                    ORDER BY c.embedding <=> CAST(:embedding AS vector)
                    LIMIT :top_k
                    """
                ),
                {"embedding": vector_literal(embedding), "top_k": top_k},
            ).mappings()
            return [
                KnowledgeChunkReference(
                    source_title=str(row["title"]),
                    source_type=str(row["source_type"]),
                    path=str(row["path"]),
                    section=(row["metadata"] or {}).get("section") if isinstance(row["metadata"], dict) else None,
                    page=(row["metadata"] or {}).get("page") if isinstance(row["metadata"], dict) else None,
                    content=str(row["content"]),
                    score=float(row["score"] or 0.0),
                    metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                )
                for row in rows
            ]


def get_knowledge_repository(settings: Settings) -> KnowledgeRepository:
    """Build the configured knowledge repository."""
    if settings.rag_store != "postgres":
        raise RuntimeError(f"Unsupported RAG store: {settings.rag_store}")
    if not settings.postgres_dsn:
        raise RuntimeError("RAG requires ARCHAGENT_POSTGRES_DSN.")
    return PostgresKnowledgeRepository(
        create_engine(settings.postgres_dsn),
        dimensions=settings.rag_embedding_dimensions,
    )


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True)

