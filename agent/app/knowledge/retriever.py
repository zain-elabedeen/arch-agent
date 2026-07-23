"""Architecture knowledge retrieval for the recommendation pipeline."""

from __future__ import annotations

from agent.app.config import Settings
from agent.app.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from agent.app.knowledge.models import KnowledgeChunkReference
from agent.app.knowledge.repository import KnowledgeRepository, get_knowledge_repository
from agent.app.logging_utils import get_logger
from agent.app.state import GraphState

logger = get_logger("agent.knowledge.retriever")


def build_knowledge_query(state: GraphState) -> str:
    """Build a retrieval query from deterministic pipeline outputs."""
    smells = ", ".join(sorted({str(smell.get("type", "")) for smell in state.get("smells", []) if smell.get("type")}))
    patterns = ", ".join(sorted({getattr(pattern, "id", "") for pattern in state.get("patterns", []) if getattr(pattern, "id", "")}))
    recommendations = ", ".join(sorted({rec.pattern for rec in state.get("recommendations", [])}))
    topology = state.get("topology", {}) or {}
    services = ", ".join((topology.get("services") or [])[:8]) if isinstance(topology, dict) else ""
    log_summary = ""
    log_analysis = state.get("log_analysis", {}) or {}
    if isinstance(log_analysis, dict):
        log_summary = str(log_analysis.get("summary") or log_analysis.get("category") or "")

    return " ".join(
        part
        for part in [
            "Architecture guidance for runtime infrastructure analysis.",
            f"Smells: {smells}." if smells else "",
            f"Patterns: {patterns or recommendations}." if patterns or recommendations else "",
            f"Services: {services}." if services else "",
            f"Log context: {log_summary}." if log_summary else "",
        ]
        if part
    )


def retrieve_knowledge_context(
    state: GraphState,
    settings: Settings,
    *,
    repository: KnowledgeRepository | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> list[KnowledgeChunkReference]:
    """Retrieve relevant architecture knowledge chunks for a pipeline state."""
    try:
        query = build_knowledge_query(state)
        if not query.strip():
            return []
        results: list[KnowledgeChunkReference] = []
        if settings.rag_enabled:
            provider = embedding_provider or get_embedding_provider(settings)
            repo = repository or get_knowledge_repository(settings)
            embedding = provider.embed_query(query)
            results.extend(repo.search(embedding, top_k=settings.rag_top_k))
        organization_id = str(state.get("organization_id") or "")
        if organization_id:
            from agent.app.product.store import get_product_store

            results.extend(get_product_store().search_knowledge(organization_id, query, top_k=8))
        deduplicated: list[KnowledgeChunkReference] = []
        seen: set[tuple[str, str]] = set()
        for result in sorted(results, key=lambda item: item.score, reverse=True):
            key = (result.source_title, result.content)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(result)
        results = deduplicated[:8]
        logger.info(
            "knowledge retrieval done run_id=%s results=%d",
            state.get("run_id", "n/a"),
            len(results),
        )
        return results
    except Exception as exc:
        logger.warning(
            "knowledge retrieval skipped run_id=%s error_type=%s message=%s",
            state.get("run_id", "n/a"),
            exc.__class__.__name__,
            str(exc),
        )
        return []
