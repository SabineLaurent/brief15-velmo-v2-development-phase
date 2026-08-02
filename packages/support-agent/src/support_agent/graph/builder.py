"""Assemble the explicit support graph.

A `StateGraph` with named nodes and conditional edges, rather than a prebuilt
`create_agent`.

    START ─► guard_input ─┬─(blocked)──────────────────────────────► END
                          ├─(human owns the case)─► human_takeover ─┐
                          └─► compact ─► router ─┬─(answer)─► answer ┤
                                                 ├─(support)─► model ⇄ tools ─┼─► guard_output ─► close_turn ─► END
                                                 └─(escalate)► escalate ──────┘

`escalate` does NOT pause the graph: it files a ticket, sets `handled_by_human` and ends
the turn, so every later message on that thread takes the `human_takeover` arrow.
`guard_input` and `guard_output` screen the message in and the reply out, both gated by
the `GUARDRAILS_ENABLED` kill switch. `compact` bounds the context window before the LLM
nodes read the history. `close_turn` flags the thread for episodic learning with one
store upsert and no model call.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from support_agent.graph.nodes import (
    ANSWER_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    SUPPORT_SYSTEM_PROMPT,
    EpisodicRecall,
    entry_route,
    human_takeover,
    make_close_turn,
    make_escalate,
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
from support_agent.guardrails import (
    build_input_guard,
    build_output_guard,
    build_tool_guard,
)
from support_agent.knowledge import build_faq_tool, build_vector_store
from support_agent.llm import (
    get_chat_model,
    get_chat_model_fallbacks,
    get_fast_chat_model,
)
from support_agent.memory import (
    AgentContext,
    build_memory_tools,
    get_checkpointer,
    get_store,
)
from support_agent.memory.compaction import make_compact


def _entry_paths(first_hop: str) -> dict:
    """Destinations of the entry conditional edge, as an explicit `path_map`.

    Declared explicitly so the drawn graph shows these three arrows and not one to every
    node. The map is also what lets `compact` slot in front of the router without
    touching `entry_route`, which keeps returning "router".
    """
    return {END: END, "human_takeover": "human_takeover", "router": first_hop}


def build_support_graph(*, learn_from_turns: bool = True) -> CompiledStateGraph:
    """Build and compile the explicit support agent graph.

    Args:
        learn_from_turns: whether this graph may FEED episodic memory (the
            `close_turn` node). False builds a read-only learner: it still recalls
            past cases, it just never records new ones. The eval harness needs
            exactly that — with learning on, every run would teach the agent from
            the very conversations used to grade it, and the measurement would
            drift upward on its own.
    """
    settings = get_settings()
    model = get_chat_model()
    fast_model = get_fast_chat_model()
    fallbacks = get_chat_model_fallbacks()
    checkpointer = get_checkpointer()
    store = get_store()

    vector_store = build_vector_store()
    backend = get_backend()
    tool_guard = (
        build_tool_guard(
            settings.guardrails_max_tool_field_chars,
            settings.guardrails_action_rate_limit,
            settings.guardrails_action_rate_window_s,
        )
        if settings.guardrails_enabled
        else None
    )
    tools = [
        build_faq_tool(vector_store),
        *build_memory_tools(tool_guard, forget_min_score=settings.forget_min_score),
        *build_action_tools(backend, tool_guard),
    ]

    episodic = (
        EpisodicRecall(
            store, settings.episodic_recall_limit, settings.episodic_min_score
        )
        if settings.episodic_memory_enabled
        else None
    )

    builder = StateGraph(SupportState, context_schema=AgentContext)

    compaction_on = settings.compact_after_messages > 0
    first_hop = "compact" if compaction_on else "router"
    if compaction_on:
        builder.add_node(
            "compact",
            make_compact(
                model,
                fallbacks,
                threshold=settings.compact_after_messages,
                keep_last=settings.compact_keep_last_messages,
            ),
        )
        builder.add_edge("compact", "router")

    builder.add_node("router", make_router(fast_model, fallbacks))
    builder.add_node("answer", make_answer(model, fallbacks))
    builder.add_node("model", make_support_model(model, tools, fallbacks, episodic))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("escalate", make_escalate(backend, tool_guard))
    builder.add_node("human_takeover", human_takeover)

    last = END
    if episodic is not None and learn_from_turns:
        builder.add_node("close_turn", make_close_turn(store))
        builder.add_edge("close_turn", END)
        last = "close_turn"

    if settings.guardrails_enabled:
        input_guard = build_input_guard(settings.guardrails_max_input_chars)
        output_guard = build_output_guard(
            [ROUTER_SYSTEM_PROMPT, ANSWER_SYSTEM_PROMPT, SUPPORT_SYSTEM_PROMPT],
            owned_email_domains=settings.guardrails_owned_email_domains.split(","),
        )
        builder.add_node("guard_input", make_guard_input(input_guard))
        builder.add_node("guard_output", make_guard_output(output_guard))
        builder.add_edge(START, "guard_input")
        builder.add_conditional_edges("guard_input", entry_route, _entry_paths(first_hop))
        builder.add_edge("guard_output", last)
        terminal = "guard_output"
    else:
        builder.add_conditional_edges(START, entry_route, _entry_paths(first_hop))
        terminal = last

    builder.add_conditional_edges(
        "router",
        route_from_state,
        {"answer": "answer", "support": "model", "escalate": "escalate"},
    )

    builder.add_conditional_edges(
        "model", tools_condition, {"tools": "tools", END: terminal}
    )
    builder.add_edge("tools", "model")

    builder.add_edge("answer", terminal)
    builder.add_edge("escalate", terminal)
    builder.add_edge("human_takeover", terminal)

    return builder.compile(checkpointer=checkpointer, store=store)
