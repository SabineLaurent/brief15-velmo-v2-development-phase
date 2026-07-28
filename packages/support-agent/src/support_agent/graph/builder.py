"""Assemble the explicit support graph (Phase 6).

This is the "capot ouvert" replacement for `create_agent`: a `StateGraph` with
named nodes and conditional edges we control ourselves.

    START ─► guard_input ─┬─(blocked)─────────────────────────────────────────────► END
                          ├─(human owns the case)─► human_takeover ─┐
                          └─► router ─┬─(answer)──► answer ─────────┤
                                      ├─(support)─► model ⇄ tools ──┼─► guard_output ─► close_turn ─► END
                                      └─(escalate)► escalate ───────┘

The `escalate` branch does NOT pause the graph: it files a ticket, sets
`handled_by_human` and ends the turn. Every later message on that thread takes
the `human_takeover` arrow — the bot is muted, the conversation stays alive.
Pausing here (an `interrupt()`) is what used to kill the thread for good: see
`make_escalate` and docs/escalade.md.

`guard_input` (Phase 12-A) validates, screens for prompt-injection and masks PII
before anything else sees the message; a refused message short-circuits to END.
`guard_output` (Phase 12-B) screens every outgoing reply — redacts leaked
PII/secrets, replaces a reply that echoes the system prompt — just before it
leaves. Both are gated by the `GUARDRAILS_ENABLED` kill switch.

`close_turn` (Phase 14) is the write side of EPISODIC memory: it flags the thread
as maybe-worth-learning-from, with one store upsert and no model call. The
distillation itself runs offline (`memory/consolidate.py`), which is why this
node can sit on the critical path without costing the customer anything.

Memory: the checkpointer keeps the conversation (short term), the store keeps
both the customer (long term, per-`user_id`) and the CASES that worked (episodic,
shared across customers), and `context_schema` carries the `user_id` the memory
tools use for per-user isolation.
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


# Destinations of the entry conditional edge. Declared explicitly (a `path_map`)
# so the drawn graph — the diagram used to EXPLAIN this agent — shows these three
# arrows and not one to every node.
_ENTRY_PATHS = {END: END, "human_takeover": "human_takeover", "router": "router"}


def build_support_graph(*, learn_from_turns: bool = True) -> CompiledStateGraph:
    """Build and compile the explicit support agent graph.

    Args:
        learn_from_turns: whether this graph may FEED episodic memory (the
            `close_turn` node). False builds a read-only learner: it still
            recalls past cases, it just never records new ones.

            The EVAL harness needs exactly that, and the reason is not tidiness.
            An eval run drives the real graph, so with learning on, every run
            would teach the agent from the very conversations used to grade it —
            and the next run would be graded against a pool the previous run
            grew. The measurement would drift upward on its own, which is the
            most flattering way for a benchmark to lie. Measuring the value of
            episodic memory is done by flipping `EPISODIC_MEMORY_ENABLED`
            deliberately, never by letting the harness write.
    """
    settings = get_settings()
    # Latency (see docs/latence.md): the ROUTER runs on the FAST model — the one
    # place a small model measurably cut TTFT (short prompt, easy classification).
    # Everything else stays on the STRONG model: measurement showed the fast model
    # did not help (and even hurt) the tool-bound support passes on our infra, where
    # the cost is the round-trip + long prompt, not the model size. With no fast
    # model configured, `fast_model` IS `model`, so the graph behaves as before.
    model = get_chat_model()
    fast_model = get_fast_chat_model()
    # Optional secondary provider(s): if the primary is fully down, the LLM nodes
    # fall over to these instead of crashing the turn (empty list = no fallback).
    fallbacks = get_chat_model_fallbacks()
    checkpointer = get_checkpointer()  # short-term: this conversation (thread_id)
    store = get_store()  # long-term: this customer (user_id)

    # Tools available on the SUPPORT branch: FAQ retrieval (read), long-term
    # memory (read/write), and business actions (order lookup + ticket creation).
    vector_store = build_vector_store()
    backend = get_backend()  # the business port: swap the adapter, not the tools
    # Phase 12-C: harden the WRITE tools (validate fields, mask PII before it is
    # persisted, rate-limit ticket creation). `None` when guardrails are off keeps
    # the tools behaving exactly as before.
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
        *build_memory_tools(tool_guard),
        *build_action_tools(backend, tool_guard),
    ]

    # Phase 14: episodic memory. Read side = a few-shot block appended to the
    # support prompt; write side = a `close_turn` node flagging the thread for
    # later distillation. Both hang off the SAME kill switch, because half of the
    # loop is worse than none: recalling without ever writing gives a memory that
    # never fills, writing without recalling pays for cases nobody reads.
    episodic = (
        EpisodicRecall(
            store, settings.episodic_recall_limit, settings.episodic_min_score
        )
        if settings.episodic_memory_enabled
        else None
    )

    # `context_schema` lets nodes and tools read the runtime `user_id`.
    builder = StateGraph(SupportState, context_schema=AgentContext)

    # Router-only cascade: the fast model classifies the intent; small talk and the
    # support ReAct loop stay on the strong model (see docs/latence.md).
    builder.add_node("router", make_router(fast_model, fallbacks))
    builder.add_node("answer", make_answer(model, fallbacks))
    builder.add_node("model", make_support_model(model, tools, fallbacks, episodic))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("escalate", make_escalate(backend, tool_guard))
    builder.add_node("human_takeover", human_takeover)

    # The LAST hop of every answering path. `close_turn` writes nothing the
    # customer sees — it is bookkeeping (one store upsert, no LLM, no embedding) —
    # so it sits after the exit guard rather than before it: nothing it does can
    # delay or alter the reply. A blocked input never reaches it, by construction:
    # that path short-circuits to END from the entry edge.
    last = END
    if episodic is not None and learn_from_turns:
        builder.add_node("close_turn", make_close_turn(store))
        builder.add_edge("close_turn", END)
        last = "close_turn"

    # Guardrails (Phase 12): the kill switch keeps the graph identical to before
    # when disabled. When enabled, the entry guard (12-A) sits BEFORE the router
    # and the exit guard (12-B) sits on every branch that answers the customer, so
    # every reply is screened just before it leaves. `terminal` is where the three
    # answering branches point: the exit guard when on, END otherwise.
    if settings.guardrails_enabled:
        input_guard = build_input_guard(settings.guardrails_max_input_chars)
        output_guard = build_output_guard(
            [ROUTER_SYSTEM_PROMPT, ANSWER_SYSTEM_PROMPT, SUPPORT_SYSTEM_PROMPT],
            owned_email_domains=settings.guardrails_owned_email_domains.split(","),
        )
        builder.add_node("guard_input", make_guard_input(input_guard))
        builder.add_node("guard_output", make_guard_output(output_guard))
        builder.add_edge(START, "guard_input")
        builder.add_conditional_edges("guard_input", entry_route, _ENTRY_PATHS)
        builder.add_edge("guard_output", last)
        terminal = "guard_output"
    else:
        # Same entry decision without the guard node: the human-takeover check
        # must NOT depend on the guardrails kill switch.
        builder.add_conditional_edges(START, entry_route, _ENTRY_PATHS)
        terminal = last

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
    builder.add_edge("human_takeover", terminal)

    return builder.compile(checkpointer=checkpointer, store=store)
