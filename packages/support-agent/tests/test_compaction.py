"""Tests for context-window compaction (R4).

The five decisions that are OURS here, and that a future edit could quietly undo:

    1. below the threshold NOTHING happens — R1 keeps 30 messages verbatim;
    2. the cut NEVER separates a tool call from its results (a provider 400);
    3. an empty summary changes the prompt by exactly ZERO bytes;
    4. a failed summarization keeps the FULL history — we never drop turns we
       failed to summarize;
    5. compaction replaces the history rather than appending to it.

No network and no API key: the summarizing model is a stub, because what is under
test is the bookkeeping around the call, not the call.
"""

from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from support_agent.memory.compaction import (
    format_summary,
    make_compact,
    plan_compaction,
)


class _StubModel:
    """Stands in for a chat model: records what it was asked, returns a canned reply."""

    def __init__(self, reply: str = "Customer wants a refund on order 12345.", fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.prompts: list = []

    def invoke(self, messages):
        self.prompts.append(messages)
        if self.fail:
            raise RuntimeError("provider is down")
        return AIMessage(content=self.reply)


def _chat(count: int) -> list:
    """A plain alternating conversation, no tool traffic."""
    messages: list = []
    for index in range(count):
        if index % 2 == 0:
            messages.append(HumanMessage(content=f"customer line {index}", id=f"m{index}"))
        else:
            messages.append(AIMessage(content=f"agent line {index}", id=f"m{index}"))
    return messages


# --- 1. The threshold ------------------------------------------------------


def test_nothing_is_compacted_below_the_threshold():
    """R1 and R4 share one number: 30 held verbatim, compaction only BEYOND it."""
    plan = plan_compaction(_chat(30), threshold=30, keep_last=10)

    assert plan.is_noop
    assert len(plan.keep) == 30


def test_compaction_kicks_in_past_the_threshold():
    plan = plan_compaction(_chat(31), threshold=30, keep_last=10)

    assert not plan.is_noop
    assert len(plan.keep) == 10
    assert len(plan.summarize) == 21


def test_a_zero_threshold_disables_compaction_entirely():
    """The kill switch: `COMPACT_AFTER_MESSAGES=0` must be a true no-op."""
    assert plan_compaction(_chat(100), threshold=0, keep_last=10).is_noop


# --- 2. The sharp edge: never split a tool call ----------------------------


def test_the_cut_never_orphans_a_tool_result():
    """A `ToolMessage` with no preceding `tool_calls` is a provider 400, not a
    degraded answer. This is the failure mode the whole `_safe_cut` exists for.

    The history is built so that a NAIVE cut at `len - keep_last` lands squarely
    on a tool result.
    """
    messages = _chat(28)
    messages += [
        AIMessage(
            content="",
            id="call",
            tool_calls=[{"name": "search_faq", "args": {"q": "refund"}, "id": "tc1"}],
        ),
        ToolMessage(content="refund policy is 14 days", tool_call_id="tc1", id="res"),
        AIMessage(content="You have 14 days.", id="final"),
    ]
    assert len(messages) == 31
    # Sanity: the naive boundary really is the tool result, so this test would
    # fail if `_safe_cut` were removed.
    assert isinstance(messages[len(messages) - 2], ToolMessage)

    plan = plan_compaction(messages, threshold=30, keep_last=2)

    assert not isinstance(plan.keep[0], ToolMessage)
    assert plan.keep[0].id == "call"  # walked back to the requesting AIMessage
    # And the summarized block does not end on a dangling tool request either.
    assert plan.summarize[-1].id != "call"


def test_an_unbreakable_tool_sequence_is_left_alone():
    """When the safe cut walks all the way back to 0, we mangle nothing.

    Preferring an oversized prompt to a broken request is the deliberate trade:
    one is slow, the other fails the turn outright.
    """
    messages: list = [
        AIMessage(
            content="",
            id="call",
            tool_calls=[
                {"name": "search_faq", "args": {}, "id": f"tc{i}"} for i in range(10)
            ],
        )
    ]
    messages += [
        ToolMessage(content=f"result {i}", tool_call_id=f"tc{i}", id=f"r{i}")
        for i in range(10)
    ]

    plan = plan_compaction(messages, threshold=5, keep_last=2)

    assert plan.is_noop
    assert len(plan.keep) == len(messages)


# --- 3. A cold feature costs zero bytes ------------------------------------


def test_no_summary_means_no_prompt_change():
    """Byte-for-byte identical prompts on every short conversation — this is what
    keeps the provider's prompt cache intact in the common case."""
    assert format_summary("") == ""
    assert format_summary("   ") == ""


def test_a_summary_is_injected_as_untrusted_data():
    """The summary is distilled from customer speech, so it must be framed as DATA
    — the same precaution the FAQ and episodic blocks get."""
    block = format_summary("Customer wants a refund.")

    assert "Customer wants a refund." in block
    assert "DATA" in block


# --- 4 & 5. The node ------------------------------------------------------


def test_the_node_replaces_the_history_and_stores_the_summary():
    model = _StubModel()
    node = make_compact(model, threshold=30, keep_last=10)

    update = node({"messages": _chat(31), "summary": ""})

    assert update["summary"] == "Customer wants a refund on order 12345."
    # REMOVE_ALL_MESSAGES first, then the kept tail: a REPLACE, not an append.
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert len(update["messages"]) == 11
    assert [m.id for m in update["messages"][1:]] == [f"m{i}" for i in range(21, 31)]


def test_the_node_merges_into_a_previous_summary():
    """A second compaction must produce ONE summary, not a chain of them."""
    model = _StubModel()
    node = make_compact(model, threshold=30, keep_last=10)

    node({"messages": _chat(31), "summary": "Earlier: customer is a pro account."})

    (prompt,) = model.prompts
    request = prompt[-1]["content"]
    assert "Earlier: customer is a pro account." in request


def test_a_failed_summarization_keeps_the_full_history():
    """The trade we refuse: losing turns to save a prompt. On a provider error the
    turn must behave exactly as it did before this feature existed."""
    node = make_compact(_StubModel(fail=True), threshold=30, keep_last=10)

    assert node({"messages": _chat(31), "summary": ""}) == {}


def test_an_empty_summary_keeps_the_full_history():
    """Trading 21 real messages for an empty string is worse than not compacting."""
    node = make_compact(_StubModel(reply="   "), threshold=30, keep_last=10)

    assert node({"messages": _chat(31), "summary": ""}) == {}


def test_the_entry_path_map_can_redirect_router_to_compact():
    """The assumption the whole wiring rests on, exercised for real.

    `entry_route` keeps returning the string "router"; the builder's `path_map`
    remaps that value to the "compact" NODE. If LangGraph ever stopped treating a
    `path_map` as value->node (rather than requiring the returned string to BE the
    node name), the compact node would be silently skipped — the agent would keep
    answering, prompts would keep growing, and no test would notice.
    """
    from langgraph.graph import END, START, StateGraph

    from support_agent.graph.builder import _entry_paths

    visited: list[str] = []

    def compact(state):
        visited.append("compact")
        return {}

    def router(state):
        visited.append("router")
        return {}

    builder = StateGraph(dict)
    builder.add_node("compact", compact)
    builder.add_node("router", router)
    # The real `path_map` declares three destinations, so the graph must offer all
    # three — which is itself worth knowing: this test uses the production map, not
    # a lookalike.
    builder.add_node("human_takeover", lambda _s: {})
    builder.add_edge("human_takeover", END)
    builder.add_conditional_edges(START, lambda _s: "router", _entry_paths("compact"))
    builder.add_edge("compact", "router")
    builder.add_edge("router", END)

    builder.compile().invoke({})

    assert visited == ["compact", "router"]


def test_the_entry_path_map_skips_compact_when_disabled():
    """With compaction off, the same edge must go straight to the router."""
    from support_agent.graph.builder import _entry_paths

    assert _entry_paths("router")["router"] == "router"


def test_the_node_does_nothing_on_a_short_conversation():
    """No LLM call at all below the threshold — this is what makes the feature free
    on every normal turn."""
    model = _StubModel()
    node = make_compact(model, threshold=30, keep_last=10)

    assert node({"messages": _chat(10), "summary": ""}) == {}
    assert model.prompts == []
