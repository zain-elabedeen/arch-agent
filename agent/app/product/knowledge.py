"""Scoped uploaded-document ingestion for local product flows."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from agent.app.config import get_settings
from agent.app.knowledge.chunking import chunk_document
from agent.app.knowledge.extractors import extract_document
from agent.app.knowledge.embeddings import get_embedding_provider
from agent.app.product.scanning import get_document_scanner
from agent.app.product.storage import StorageBackend, get_storage_backend
from agent.app.product.store import ProductStore, get_product_store


def process_document(document_id: str, *, store: ProductStore | None = None, storage: StorageBackend | None = None) -> None:
    store = store or get_product_store()
    storage = storage or get_storage_backend()
    document = store.get_document(document_id, internal=True)
    if not document:
        raise LookupError("document_not_found")

    job = store.create_ingestion_job(document_id)
    error_code = "processing_failed"
    try:
        content = storage.read_bytes(document["object_key"])
        error_code = "scan_failed"
        get_document_scanner().scan(document["filename"], content)
        error_code = "extraction_failed"
        store.update_document(document_id, internal=True, status="indexing", byte_size=len(content), checksum=hashlib.sha256(content).hexdigest())
        source_path = storage.local_path(document["object_key"])
        if source_path is None:
            with tempfile.NamedTemporaryFile(suffix=Path(document["filename"]).suffix) as temp:
                temp.write(content)
                temp.flush()
                extracted = extract_document(temp.name)
        else:
            extracted = extract_document(source_path)
        extracted.title = document["title"]
        extracted.path = document["filename"]
        chunks = chunk_document(
            extracted,
            chunk_tokens=get_settings().rag_chunk_tokens,
            overlap_tokens=get_settings().rag_chunk_overlap_tokens,
        )
        settings = get_settings()
        embeddings = get_embedding_provider(settings).embed_texts([chunk.content for chunk in chunks]) if settings.rag_enabled else None
        store.replace_chunks(document, chunks, embeddings=embeddings)
        store.update_document(document_id, internal=True, status="draft" if document["scope"] == "global" else "ready")
        store.complete_ingestion_job(job["id"], status="completed")
    except Exception:
        try:
            storage.quarantine(document["object_key"])
        except Exception:
            pass
        store.update_document(document_id, internal=True, status="failed")
        store.complete_ingestion_job(job["id"], status="failed", error_code=error_code)
        raise
