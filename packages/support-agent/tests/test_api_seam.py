"""Contract tests for the API seam (`api.stream_reply`).

They hold the invariant that keeps two customer-visible bugs fixed — the output guard
being bypassed in streaming, and escalation delivering an empty bubble:

    every path yields EXACTLY ONE non-empty chunk

The rule shipped in prose only, so the next refactor could quietly undo it.

Pure UNIT tests: the graph is replaced by a fake whose `invoke` returns a crafted
terminal state. We are testing the seam's reading of the state, not the agent's
thinking.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from support_agent import api
from support_agent.graph.nodes import (
    ESCALATION_PENDING_MESSAGE,
    GRACEFUL_ERROR_MESSAGE,
)


class _FakeAgent:
    """Stands in for the compiled graph: returns a canned terminal state."""

    def __init__(self, result: dict | Exception) -> None:
        self._result = result

    def invoke(self, inputs: dict, **kwargs: Any) -> dict:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _collect(result: dict | Exception) -> list[str]:
    """Drain `stream_reply` against a faked graph and return the chunks."""

    async def _run() -> list[str]:
        return [
            chunk
            async for chunk in api.stream_reply(
                "Bonjour", user_id="u-1", thread_id="t-1"
            )
        ]

    original = api.get_agent
    api.get_agent = lambda: _FakeAgent(result)  # type: ignore[assignment]
    try:
        return asyncio.run(_run())
    finally:
        api.get_agent = original  # type: ignore[assignment]


# --- The invariant itself ----------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"messages": [AIMessage(content="Voici votre réponse.")]}, id="normal"),
        pytest.param({"__interrupt__": [object()]}, id="escalation-paused"),
        pytest.param({"messages": []}, id="no-message-at-all"),
        pytest.param({"messages": [HumanMessage(content="Bonjour")]}, id="human-last"),
        pytest.param({"messages": [AIMessage(content="")]}, id="empty-ai-content"),
        pytest.param(RuntimeError("checkpointer exploded"), id="graph-crash"),
    ],
)
def test_every_path_yields_exactly_one_non_empty_chunk(result: dict | Exception) -> None:
    """THE invariant: no front may ever face an empty stream, whatever happened."""
    chunks = _collect(result)
    assert len(chunks) == 1
    assert chunks[0].strip()


# --- What each path delivers -------------------------------------------------


def test_normal_reply_is_delivered_verbatim() -> None:
    """The happy path hands over the graph's final message, untouched.

    This is also the guard-blocked path: `guard_output` replaces the message
    inside the graph, so from the seam's point of view a redacted reply is just
    the terminal `AIMessage`. That is precisely why guarding works now.
    """
    assert _collect({"messages": [AIMessage(content="Livraison en 48 h.")]}) == [
        "Livraison en 48 h."
    ]


def test_paused_escalation_announces_the_handoff() -> None:
    """A graph paused on `escalate` has no AI reply yet — say so, do not go silent."""
    assert _collect({"__interrupt__": [object()]}) == [ESCALATION_PENDING_MESSAGE]


def test_interrupt_wins_over_a_stale_reply() -> None:
    """Ordering bug guard: an interrupted run still carries the PREVIOUS turn.

    If the seam looked at `messages` before `__interrupt__`, it would replay last
    turn's answer as if it were this turn's — the customer would think the agent
    ignored the request that triggered the escalation. Order is load-bearing.
    """
    result = {
        "__interrupt__": [object()],
        "messages": [AIMessage(content="Réponse du tour PRÉCÉDENT.")],
    }
    assert _collect(result) == [ESCALATION_PENDING_MESSAGE]


def test_graph_crash_degrades_gracefully() -> None:
    """Infrastructure failure must not surface as a stack trace or a dead stream."""
    assert _collect(RuntimeError("store is down")) == [GRACEFUL_ERROR_MESSAGE]


def test_missing_reply_degrades_gracefully() -> None:
    """A state with no usable AIMessage should not happen — cover it anyway."""
    assert _collect({"messages": [HumanMessage(content="Bonjour")]}) == [
        GRACEFUL_ERROR_MESSAGE
    ]


# --- The keys the seam is responsible for -----------------------------------


def test_thread_and_user_ids_reach_the_graph() -> None:
    """Memory isolation depends on these two keys actually being passed through.

    `thread_id` must land in `config.configurable` (short-term memory) and
    `user_id` in the runtime context (long-term memory). A regression here would
    not crash anything — it would silently merge customers' memories, which is
    the failure mode the `no_cross_user_leak` evaluator exists to catch.
    """
    captured: dict[str, Any] = {}

    class _CapturingAgent:
        def invoke(self, inputs: dict, **kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"messages": [AIMessage(content="ok")]}

    async def _run() -> None:
        async for _ in api.stream_reply("Bonjour", user_id="u-42", thread_id="t-99"):
            pass

    original = api.get_agent
    api.get_agent = lambda: _CapturingAgent()  # type: ignore[assignment]
    try:
        asyncio.run(_run())
    finally:
        api.get_agent = original  # type: ignore[assignment]

    assert captured["config"]["configurable"]["thread_id"] == "t-99"
    assert captured["context"].user_id == "u-42"
