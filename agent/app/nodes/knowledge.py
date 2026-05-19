"""LangGraph node that retrieves architecture knowledge context for RAG."""

from __future__ import annotations

from agent.app.config import Settings
from agent.app.knowledge.retriever import retrieve_knowledge_context
from agent.app.logging_utils import get_logger
from agent.app.state import GraphState

logger = get_logger("agent.nodes.knowledge")


def knowledge_retrieval_node(state: GraphState, settings: Settings) -> GraphState:
    """Attach retrieved architecture knowledge chunks to the graph state."""
    run_id = state.get("run_id", "n/a")
    logger.info("knowledge_retrieval_agent start run_id=%s enabled=%s", run_id, settings.rag_enabled)
    state["knowledge_context"] = retrieve_knowledge_context(state, settings)
    logger.info(
        "knowledge_retrieval_agent done run_id=%s chunks=%d",
        run_id,
        len(state.get("knowledge_context", [])),
    )
    return state

