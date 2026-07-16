"""Assemble the explicit support graph (Phase 6).

This is the "capot ouvert" replacement for `create_agent`: a `StateGraph` with
named nodes and conditional edges we control ourselves.

    START ─► router ─┬─(answer)──► answer ─────────────────► END
                     ├─(support)─► model ⇄ tools ─(ReAct)──► END
                     └─(escalate)► escalate ─(interrupt ⏸)─► END

Memory is preserved exactly as before: the checkpointer keeps the conversation
(short term), the store keeps the customer (long term), and `context_schema`
carries the `user_id` the memory tools use for per-user isolation.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from support_agent.graph.nodes import (
    escalate,
    make_answer,
    make_router,
    make_support_model,
    route_from_state,
)
from support_agent.actions import build_action_tools, get_backend
from support_agent.graph.state import SupportState
from support_agent.knowledge import build_faq_tool, build_vector_store
from support_agent.llm import get_chat_model, get_chat_model_fallbacks
from support_agent.memory import (
    AgentContext,
    build_memory_tools,
    get_checkpointer,
    get_store,
)


def build_support_graph() -> CompiledStateGraph:
    """Build and compile the explicit support agent graph."""
    model = get_chat_model()
    # Optional secondary provider(s): if the primary is fully down, the LLM nodes
    # fall over to these instead of crashing the turn (empty list = no fallback).
    fallbacks = get_chat_model_fallbacks()
    checkpointer = get_checkpointer()  # short-term: this conversation (thread_id)
    store = get_store()  # long-term: this customer (user_id)

    # Tools available on the SUPPORT branch: FAQ retrieval (read), long-term
    # memory (read/write), and business actions (order lookup + ticket creation).
    vector_store = build_vector_store()
    backend = get_backend()  # the business port: swap the adapter, not the tools
    tools = [
        build_faq_tool(vector_store),
        *build_memory_tools(),
        *build_action_tools(backend),
    ]

    # `context_schema` lets nodes and tools read the runtime `user_id`.
    builder = StateGraph(SupportState, context_schema=AgentContext)

    builder.add_node("router", make_router(model, fallbacks))
    builder.add_node("answer", make_answer(model, fallbacks))
    builder.add_node("model", make_support_model(model, tools, fallbacks))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("escalate", escalate)

    # START always goes through the router first.
    builder.add_edge(START, "router")

    # The routing decision: the conditional edge maps each `route` value to a node.
    builder.add_conditional_edges(
        "router",
        route_from_state,
        {"answer": "answer", "support": "model", "escalate": "escalate"},
    )

    # The SUPPORT branch is a ReAct loop: model -> (tools -> model)* -> END.
    # `tools_condition` returns "tools" if the LLM asked for a tool, else END.
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")

    # The two leaf branches end the turn.
    builder.add_edge("answer", END)
    builder.add_edge("escalate", END)

    return builder.compile(checkpointer=checkpointer, store=store)
