"""Assemble the explicit support graph (Phase 6).

This is the "capot ouvert" replacement for `create_agent`: a `StateGraph` with
named nodes and conditional edges we control ourselves.

    START ─► guard_input ─┬─(blocked)──────────────────────────────────────► END
                          └─► router ─┬─(answer)──► answer ────┐
                                      ├─(support)─► model ⇄ tools ─(ReAct)─┼─► guard_output ─► END
                                      └─(escalate)► escalate ─(interrupt ⏸)┘

`guard_input` (Phase 12-A) validates, screens for prompt-injection and masks PII
before anything else sees the message; a refused message short-circuits to END.
`guard_output` (Phase 12-B) screens every outgoing reply — redacts leaked
PII/secrets, replaces a reply that echoes the system prompt — just before it
leaves. Both are gated by the `GUARDRAILS_ENABLED` kill switch.

Memory is preserved exactly as before: the checkpointer keeps the conversation
(short term), the store keeps the customer (long term), and `context_schema`
carries the `user_id` the memory tools use for per-user isolation.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from support_agent.graph.nodes import (
    ANSWER_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    SUPPORT_SYSTEM_PROMPT,
    escalate,
    guard_route,
    make_answer,
    make_guard_input,
    make_guard_output,
    make_router,
    make_support_model,
    route_from_state,
)
from support_agent.actions import build_action_tools, get_backend
from support_agent.config import get_settings
from support_agent.graph.state import SupportState
from support_agent.guardrails import build_input_guard, build_output_guard
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
    settings = get_settings()
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

    # Guardrails (Phase 12): the kill switch keeps the graph identical to before
    # when disabled. When enabled, the entry guard (12-A) sits BEFORE the router
    # and the exit guard (12-B) sits on every branch that answers the customer, so
    # every reply is screened just before it leaves. `terminal` is where the three
    # answering branches point: the exit guard when on, END otherwise.
    if settings.guardrails_enabled:
        input_guard = build_input_guard(settings.guardrails_max_input_chars)
        output_guard = build_output_guard(
            [ROUTER_SYSTEM_PROMPT, ANSWER_SYSTEM_PROMPT, SUPPORT_SYSTEM_PROMPT]
        )
        builder.add_node("guard_input", make_guard_input(input_guard))
        builder.add_node("guard_output", make_guard_output(output_guard))
        builder.add_edge(START, "guard_input")
        builder.add_conditional_edges("guard_input", guard_route)
        builder.add_edge("guard_output", END)
        terminal = "guard_output"
    else:
        builder.add_edge(START, "router")
        terminal = END

    # The routing decision: the conditional edge maps each `route` value to a node.
    builder.add_conditional_edges(
        "router",
        route_from_state,
        {"answer": "answer", "support": "model", "escalate": "escalate"},
    )

    # The SUPPORT branch is a ReAct loop: model -> (tools -> model)* -> terminal.
    # `tools_condition` returns "tools" if the LLM asked for a tool, else END —
    # we remap that END to the exit guard (or real END when guardrails are off).
    builder.add_conditional_edges(
        "model", tools_condition, {"tools": "tools", END: terminal}
    )
    builder.add_edge("tools", "model")

    # The two leaf branches end the turn (through the exit guard when enabled).
    builder.add_edge("answer", terminal)
    builder.add_edge("escalate", terminal)

    return builder.compile(checkpointer=checkpointer, store=store)
