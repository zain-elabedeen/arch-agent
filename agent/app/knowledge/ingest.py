"""CLI for ingesting architecture knowledge into the RAG index."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.app.config import get_settings
from agent.app.knowledge.chunking import chunk_document
from agent.app.knowledge.embeddings import get_embedding_provider
from agent.app.knowledge.extractors import extract_document, supported_files
from agent.app.knowledge.models import IngestionResult
from agent.app.knowledge.repository import get_knowledge_repository


def ingest_path(path: str | Path) -> IngestionResult:
    """Ingest one file or directory into the configured knowledge repository."""
    settings = get_settings()
    provider = get_embedding_provider(settings)
    repo = get_knowledge_repository(settings)
    result = IngestionResult()

    for file_path in supported_files(Path(path)):
        result.files_scanned += 1
        try:
            document = extract_document(file_path)
            chunks = chunk_document(
                document,
                chunk_tokens=settings.rag_chunk_tokens,
                overlap_tokens=settings.rag_chunk_overlap_tokens,
            )
            result.chunks_seen += len(chunks)
            embeddings = provider.embed_texts([chunk.content for chunk in chunks])
            indexed, skipped = repo.upsert_chunks(chunks, embeddings)
            result.chunks_indexed += indexed
            result.chunks_skipped += skipped
        except Exception as exc:
            result.errors.append(f"{file_path}: {exc}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest architecture knowledge into ArchAgent RAG.")
    parser.add_argument("--path", default=None, help="File or directory to ingest. Defaults to ARCHAGENT_RAG_KNOWLEDGE_PATH.")
    parser.add_argument("--file", default=None, help="Single file to ingest.")
    args = parser.parse_args()

    settings = get_settings()
    target = args.file or args.path or settings.rag_knowledge_path
    result = ingest_path(target)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()

