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

    g.add_node("telemetry", telemetry_node)
    g.add_node("smells", smells_node)
    g.add_node("retrieval", lambda s: retrieval_node(s, settings))
    g.add_node("recommend", recommend_node)
    g.add_node("critic", critic_node)
    g.add_node("planner", planner_node)

    g.set_entry_point("telemetry")
    g.add_edge("telemetry", "smells")
    g.add_edge("smells", "retrieval")
    g.add_edge("retrieval", "recommend")
    g.add_edge("recommend", "critic")
    g.add_edge("critic", "planner")
    g.add_edge("planner", END)

    return g.compile()

