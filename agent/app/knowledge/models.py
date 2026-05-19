"""Typed models for architecture knowledge chunks and retrieval results."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KnowledgeDocument(BaseModel):
    """Raw extracted architecture knowledge from one source file."""

    title: str
    source_type: str
    path: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    """One indexable chunk from a knowledge source."""

    source_title: str
    source_type: str
    path: str
    chunk_index: int
    content: str
    content_hash: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunkReference(BaseModel):
    """Retrieved chunk attached to ``GraphState`` for explanation enrichment."""

    source_title: str
    source_type: str
    path: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    content: str
    score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IngestionResult(BaseModel):
    """Summary returned by the ingestion CLI/service."""

    files_scanned: int = 0
    chunks_seen: int = 0
    chunks_indexed: int = 0
    chunks_skipped: int = 0
    errors: List[str] = Field(default_factory=list)

