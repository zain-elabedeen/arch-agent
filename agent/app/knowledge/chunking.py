"""Chunking helpers for architecture knowledge ingestion."""

from __future__ import annotations

import hashlib
import re
from typing import List

from agent.app.knowledge.models import KnowledgeChunk, KnowledgeDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_PAGE_RE = re.compile(r"^\[page\s+(\d+)\]$", re.IGNORECASE)


def chunk_document(
    document: KnowledgeDocument,
    *,
    chunk_tokens: int = 1000,
    overlap_tokens: int = 180,
) -> List[KnowledgeChunk]:
    """
    Split a document into overlapping word-token chunks.

    This deliberately uses a dependency-free token approximation.
    """
    words_with_meta = _words_with_context(document.text)
    if not words_with_meta:
        return []

    chunk_tokens = max(100, chunk_tokens)
    overlap_tokens = max(0, min(overlap_tokens, chunk_tokens // 2))
    step = chunk_tokens - overlap_tokens
    chunks: list[KnowledgeChunk] = []

    for index, start in enumerate(range(0, len(words_with_meta), step)):
        window = words_with_meta[start : start + chunk_tokens]
        if not window:
            continue
        content = " ".join(item[0] for item in window).strip()
        if not content:
            continue
        section = next((item[1] for item in reversed(window) if item[1]), None)
        page = next((item[2] for item in reversed(window) if item[2]), None)
        metadata = dict(document.metadata)
        if section:
            metadata["section"] = section
        if page:
            metadata["page"] = page
        chunks.append(
            KnowledgeChunk(
                source_title=document.title,
                source_type=document.source_type,
                path=document.path,
                chunk_index=index,
                content=content,
                content_hash=stable_content_hash(document.path, content),
                metadata=metadata,
            )
        )
        if start + chunk_tokens >= len(words_with_meta):
            break
    return chunks


def stable_content_hash(path: str, content: str) -> str:
    """Stable hash used to skip unchanged chunks."""
    normalized = " ".join(content.split())
    return hashlib.sha256(f"{path}\n{normalized}".encode("utf-8")).hexdigest()


def _words_with_context(text: str) -> list[tuple[str, str | None, int | None]]:
    current_section: str | None = None
    current_page: int | None = None
    items: list[tuple[str, str | None, int | None]] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            current_section = heading.group(2).strip()
            continue
        page = _PAGE_RE.match(stripped)
        if page:
            current_page = int(page.group(1))
            continue
        for word in stripped.split():
            items.append((word, current_section, current_page))
    return items

