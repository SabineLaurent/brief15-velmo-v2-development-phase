"""Orchestration layer: the explicit LangGraph `StateGraph`.

Replaces the prebuilt `create_agent` with a graph we control: a router that
classifies intent, then routes to a small-talk answer, the support ReAct loop
(FAQ + memory), or a human escalation.
"""

from support_agent.graph.builder import build_support_graph
from support_agent.graph.state import SupportState

__all__ = ["build_support_graph", "SupportState"]
