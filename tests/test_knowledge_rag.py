from pathlib import Path

from agent.app.config import Settings
from agent.app.knowledge.chunking import chunk_document
from agent.app.knowledge.embeddings import HashEmbeddingProvider
from agent.app.knowledge.extractors import extract_document
from agent.app.knowledge.models import KnowledgeChunkReference
from agent.app.knowledge.retriever import build_knowledge_query, retrieve_knowledge_context
from agent.app.nodes.knowledge import knowledge_retrieval_node
from agent.app.nodes.reasoning import build_explanation_report
from agent.app.state import Recommendation


def test_markdown_extraction_and_chunking_preserve_context(tmp_path: Path):
    source = tmp_path / "resilience-notes.md"
    source.write_text(
        "# Resilience\n\n"
        "Horizontal scaling adds more service instances for availability.\n\n"
        "Load balancing distributes requests across healthy replicas.\n",
        encoding="utf-8",
    )

    document = extract_document(source)
    chunks = chunk_document(document, chunk_tokens=12, overlap_tokens=3)

    assert document.title == "resilience notes"
    assert document.source_type == "markdown"
    assert chunks
    assert chunks[0].metadata["section"] == "Resilience"
    assert "Horizontal scaling" in chunks[0].content
    assert len({chunk.content_hash for chunk in chunks}) == len(chunks)


def test_build_knowledge_query_uses_pipeline_outputs():
    state = {
        "smells": [{"type": "single_instance_risk"}],
        "patterns": [],
        "recommendations": [
            Recommendation(
                pattern="horizontal_scaling",
                solution="Add replicas",
                impact="high",
                effort="medium",
                reason="Remove single-replica availability risk",
            )
        ],
        "topology": {"services": ["api"]},
    }

    query = build_knowledge_query(state)

    assert "single_instance_risk" in query
    assert "horizontal_scaling" in query
    assert "api" in query


def test_retrieve_knowledge_context_can_use_injected_dependencies():
    class FakeRepository:
        def search(self, embedding, *, top_k):
            assert top_k == 2
            assert len(embedding) == 16
            return [
                KnowledgeChunkReference(
                    source_title="Release It",
                    source_type="book",
                    content="Use bulkheads to isolate failure domains.",
                    score=0.91,
                )
            ]

    settings = Settings(rag_enabled=True, rag_embedding_provider="hash", rag_embedding_dimensions=16, rag_top_k=2)
    state = {"run_id": "test", "smells": [{"type": "dependency_instability"}], "recommendations": []}

    results = retrieve_knowledge_context(
        state,
        settings,
        repository=FakeRepository(),
        embedding_provider=HashEmbeddingProvider(dimensions=16),
    )

    assert len(results) == 1
    assert results[0].source_title == "Release It"


def test_knowledge_node_is_noop_when_rag_disabled():
    settings = Settings(rag_enabled=False)
    out = knowledge_retrieval_node({"run_id": "test"}, settings)

    assert out["knowledge_context"] == []


def test_reasoning_report_includes_knowledge_citations():
    state = {
        "smells": [{"type": "single_instance_risk", "severity": "medium", "confidence": 0.74}],
        "recommendations": [],
        "critiques": [],
        "knowledge_context": [
            KnowledgeChunkReference(
                source_title="Architecture Notes",
                source_type="markdown",
                section="Availability",
                content="Single replicas are availability risks for critical services.",
                score=0.88,
            )
        ],
    }

    report = build_explanation_report(state)

    assert "Relevant Architecture Knowledge" in report
    assert "Architecture Notes" in report
    assert "Single replicas are availability risks" in report

