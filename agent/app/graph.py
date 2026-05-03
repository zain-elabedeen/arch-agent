from __future__ import annotations

from typing import Callable

from langgraph.graph import END, StateGraph

from agent.app.config import Settings
from agent.app.nodes.critic import critic_node
from agent.app.nodes.planner import planner_node
from agent.app.nodes.recommend import recommend_node
from agent.app.nodes.retrieval import retrieval_node
from agent.app.nodes.smells import smells_node
from agent.app.nodes.telemetry import telemetry_node
from agent.app.state import GraphState


def build_graph(settings: Settings):
    """
    Build a LangGraph pipeline.

    Nodes are written to be callable independently; the graph provides orchestration.
    """

    g = StateGraph(GraphState)

    g.add_node("telemetry_agent", telemetry_node)
    g.add_node("smell_agent", smells_node)
    g.add_node("retrieval_agent", lambda s: retrieval_node(s, settings))
    g.add_node("recommendation_agent", recommend_node)
    g.add_node("critic_agent", critic_node)
    g.add_node("planner_agent", planner_node)

    g.set_entry_point("telemetry_agent")
    g.add_edge("telemetry_agent", "smell_agent")
    g.add_edge("smell_agent", "retrieval_agent")
    g.add_edge("retrieval_agent", "recommendation_agent")
    g.add_edge("recommendation_agent", "critic_agent")
    g.add_edge("critic_agent", "planner_agent")
    g.add_edge("planner_agent", END)

    return g.compile()

