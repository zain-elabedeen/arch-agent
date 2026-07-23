"""Embedding providers for architecture knowledge RAG."""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol

from agent.app.config import Settings


class EmbeddingProvider(Protocol):
    """Minimal embedding provider interface used by ingestion and retrieval."""

    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbeddingProvider:
    """OpenAI embedding provider."""

    def __init__(self, *, api_key: str, model: str, dimensions: int):
        from openai import OpenAI  # type: ignore[reportMissingImports]

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in response.data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class GoogleCloudEmbeddingProvider:
    """Google Cloud Vertex AI embedding provider via the Gen AI SDK."""

    def __init__(self, *, project_id: str, location: str, model: str, dimensions: int, api_version: str):
        from google import genai  # type: ignore[reportMissingImports]
        from google.genai.types import HttpOptions  # type: ignore[reportMissingImports]

        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
            http_options=HttpOptions(api_version=api_version),
        )
        self.model = model
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text, task_type="RETRIEVAL_DOCUMENT") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text, task_type="RETRIEVAL_QUERY")

    def _embed(self, text: str, *, task_type: str) -> list[float]:
        from google.genai.types import EmbedContentConfig  # type: ignore[reportMissingImports]

        response = self.client.models.embed_content(
            model=self.model,
            contents=text,
            config=EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions,
            ),
        )
        embeddings = response.embeddings or []
        if not embeddings or embeddings[0].values is None:
            raise RuntimeError("Google Cloud embedding response did not include embedding values.")
        return list(embeddings[0].values)


class HashEmbeddingProvider:
    """
    Deterministic local embedding provider for tests and offline development.

    It is not semantically rich, but gives stable vectors and lets the RAG
    repository/retrieval path be tested without network access.
    """

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Build the configured embedding provider."""
    if settings.rag_embedding_provider == "hash":
        return HashEmbeddingProvider(dimensions=settings.rag_embedding_dimensions)

    if settings.rag_embedding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("ARCHAGENT_OPENAI_API_KEY is required for OpenAI RAG embeddings.")
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.rag_embedding_model,
            dimensions=settings.rag_embedding_dimensions,
        )

    project_id = _resolve_gcp_project_id(settings)
    if not project_id:
        raise RuntimeError(
            "ARCHAGENT_GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required for Google Cloud RAG embeddings."
        )
    return GoogleCloudEmbeddingProvider(
        project_id=project_id,
        location=_resolve_gcp_location(settings),
        model=settings.rag_embedding_model,
        dimensions=settings.rag_embedding_dimensions,
        api_version=settings.gcp_genai_api_version,
    )


def _resolve_gcp_project_id(settings: Settings) -> str | None:
    return (
        settings.gcp_project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    )


def _resolve_gcp_location(settings: Settings) -> str:
    return settings.gcp_location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"


def vector_literal(vector: list[float]) -> str:
    """Format a Python vector as a pgvector literal."""
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
