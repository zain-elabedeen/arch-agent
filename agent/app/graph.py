"""
LangGraph orchestration for the architecture recommendation pipeline.

Each graph node is a single-responsibility *agent* (telemetry, smells, retrieval,
recommend, critic, planner, log analysis, reasoning). They share ``GraphState``
(``agent.app.state``) as typed working memory. Architecture decisions remain
deterministic; optional LLM nodes are sidecar explanation/evidence enrichment.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent.app.config import Settings
from agent.app.logging_utils import get_logger
from agent.app.nodes.critic import critic_node
from agent.app.nodes.log_analysis import log_analysis_node
from agent.app.nodes.planner import planner_node
from agent.app.nodes.recommend import recommend_node
from agent.app.nodes.reasoning import reasoning_node
from agent.app.nodes.retrieval import retrieval_node
from agent.app.nodes.smells import smells_node
from agent.app.nodes.telemetry import telemetry_node
from agent.app.state import GraphState

logger = get_logger("agent.graph")


def build_graph(settings: Settings):
    """
    Compile the linear multi-agent pipeline: telemetry → … → log analysis → reasoning → END.

    ``retrieval_agent`` and ``reasoning_agent`` close over ``settings`` for catalog
    path and optional LLM configuration; other nodes are pure state in → state out.

    Returns:
        A compiled LangGraph runnable: ``invoke(GraphState)`` merges partial node
        returns into the terminal ``GraphState``.
    """

    logger.info("building pipeline graph for environment=%s", settings.environment)
    g = StateGraph(GraphState)

    g.add_node("telemetry_agent", telemetry_node)
    g.add_node("smell_agent", smells_node)
    g.add_node("retrieval_agent", lambda s: retrieval_node(s, settings))
    g.add_node("recommendation_agent", recommend_node)
    g.add_node("critic_agent", critic_node)
    g.add_node("planner_agent", planner_node)
    g.add_node("log_analysis_agent", lambda s: log_analysis_node(s, settings))
    g.add_node("reasoning_agent", lambda s: reasoning_node(s, settings))

    g.set_entry_point("telemetry_agent")
    g.add_edge("telemetry_agent", "smell_agent")
    g.add_edge("smell_agent", "retrieval_agent")
    g.add_edge("retrieval_agent", "recommendation_agent")
    g.add_edge("recommendation_agent", "critic_agent")
    g.add_edge("critic_agent", "planner_agent")
    g.add_edge("planner_agent", "log_analysis_agent")
    g.add_edge("log_analysis_agent", "reasoning_agent")
    g.add_edge("reasoning_agent", END)

    compiled = g.compile()
    logger.info("pipeline graph compiled nodes=%s", ["telemetry_agent", "smell_agent", "retrieval_agent", "recommendation_agent", "critic_agent", "planner_agent", "log_analysis_agent", "reasoning_agent"])
    return compiled
