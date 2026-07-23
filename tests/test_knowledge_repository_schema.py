from __future__ import annotations

from contextlib import contextmanager
import importlib
from types import SimpleNamespace
from uuid import uuid4

from agent.app.knowledge.models import KnowledgeChunk
from agent.app.knowledge.repository import PostgresKnowledgeRepository


class RecordingResult:
    def scalar_one_or_none(self):
        return None

    def scalar_one(self):
        return uuid4()


class RecordingConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, _parameters=None) -> RecordingResult:
        self.statements.append(str(statement))
        return RecordingResult()


class RecordingEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.url = f"postgresql://schema-test/{uuid4()}"
        self.connection = RecordingConnection()

    @contextmanager
    def begin(self):
        yield self.connection


def test_knowledge_repository_adds_updated_at_to_all_tables_and_upserts() -> None:
    engine = RecordingEngine()
    repository = PostgresKnowledgeRepository(engine, dimensions=3)

    migration = importlib.import_module("migrations.versions.0009_architecture_knowledge_schema")
    ddl = " ".join("\n".join(migration.ARCHITECTURE_KNOWLEDGE_SCHEMA_STATEMENTS).lower().split())
    assert "create table if not exists architecture_knowledge_sources" in ddl
    assert "create table if not exists architecture_knowledge_chunks" in ddl
    assert "alter table architecture_knowledge_sources add column if not exists updated_at" in ddl
    assert "alter table architecture_knowledge_chunks add column if not exists updated_at" in ddl

    repository.ensure_schema = lambda: None  # type: ignore[method-assign]
    repository.upsert_chunks(
        [
            KnowledgeChunk(
                source_title="Architecture Notes",
                source_type="markdown",
                path="architecture.md",
                chunk_index=0,
                content="Use bulkheads.",
                content_hash="hash",
            )
        ],
        [[0.1, 0.2, 0.3]],
    )
    upserts = " ".join("\n".join(engine.connection.statements).lower().split())
    assert upserts.count("updated_at = now()") == 2
