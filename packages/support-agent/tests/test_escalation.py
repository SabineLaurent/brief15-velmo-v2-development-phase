"""Escalation: the case goes to a human, the conversation stays alive.

Pure UNIT tests — no `.env`, no network, no LLM. The router is replaced by a stub that
always chooses `escalate`; everything downstream of it is real code.

`test_the_thread_survives_an_escalation` is the one that matters: the defect it guards
against is invisible to any single-turn test. The old `escalate` called `interrupt()`,
LangGraph resumed that pending task ahead of anything else, and EVERY later message on
the thread got the same handoff sentence, forever, with no error anywhere.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from support_agent.actions.backend import InMemorySupportBackend
from support_agent.graph.nodes import (
    ESCALATION_TICKET_SUBJECT,
    HUMAN_TAKEOVER_MESSAGE,
    TAKEOVER_RELEASED_MESSAGE,
    entry_route,
    human_takeover,
    make_escalate,
)
from support_agent.graph.state import SupportState
from support_agent.memory import AgentContext

USER = "customer-42"


@pytest.fixture
def backend() -> InMemorySupportBackend:
    """A fresh business backend per test: the global one accumulates tickets."""
    return InMemorySupportBackend()


def _graph(backend: InMemorySupportBackend):
    """The real entry edge + real escalate/takeover nodes, with a stub router.

    Deliberately NOT `build_support_graph()`: that one needs a provider key and a
    live embeddings call. What we assert here is the wiring, not the LLM.
    """
    builder = StateGraph(SupportState, context_schema=AgentContext)
    builder.add_node("router", lambda state: {"route": "escalate"})
    builder.add_node("escalate", make_escalate(backend))
    builder.add_node("human_takeover", human_takeover)
    builder.add_conditional_edges(
        START,
        entry_route,
        {END: END, "human_takeover": "human_takeover", "router": "router"},
    )
    builder.add_edge("router", "escalate")
    builder.add_edge("escalate", END)
    builder.add_edge("human_takeover", END)
    return builder.compile(checkpointer=InMemorySaver())


def _say(graph, text: str, thread_id: str = "t-1") -> dict:
    return graph.invoke(
        {"messages": [{"role": "user", "content": text}]},
        context=AgentContext(user_id=USER),
        config={"configurable": {"thread_id": thread_id}},
    )


# --- The regression the review demanded -------------------------------------


def test_the_thread_survives_an_escalation(backend) -> None:
    """Two turns on the SAME thread: the second must still be processed."""
    graph = _graph(backend)

    first = _say(graph, "je veux parler à un humain")
    assert not first.get("__interrupt__")
    assert first["handled_by_human"] is True

    second = _say(graph, "finalement, quels sont vos délais de livraison ?")

    assert not second.get("__interrupt__")
    assert second["messages"][-1].content == HUMAN_TAKEOVER_MESSAGE
    assert second["messages"][-1].content != first["messages"][-1].content
    assert any(
        "délais de livraison" in str(m.content) for m in second["messages"]
    )


def test_the_bot_stops_answering_once_a_human_owns_the_case(backend) -> None:
    """Muted, not thinking: the router is never reached again on this thread."""
    graph = _graph(backend)
    _say(graph, "je veux un conseiller")

    assert entry_route({"handled_by_human": True}) == "human_takeover"
    assert entry_route({"handled_by_human": True, "input_blocked": True}) == END
    assert entry_route({}) == "router"


# --- The case object ---------------------------------------------------------


def test_escalation_files_a_ticket_for_the_right_customer(backend) -> None:
    """The durable, listable artefact of a handoff — not a paused graph."""
    _say(_graph(backend), "je veux parler à un humain")

    tickets = backend.list_tickets(USER)
    assert len(tickets) == 1
    assert tickets[0].subject == ESCALATION_TICKET_SUBJECT
    assert "je veux parler à un humain" in tickets[0].body


def test_a_repeated_escalation_cannot_open_a_second_ticket(backend) -> None:
    """Self-limiting by construction: the flag keeps the router out of reach."""
    graph = _graph(backend)
    _say(graph, "je veux parler à un humain")
    _say(graph, "je veux parler à un humain")

    assert len(backend.list_tickets(USER)) == 1


# --- The way back out of a takeover -----------------------------------------


def test_the_customer_can_take_the_bot_back(backend) -> None:
    graph = _graph(backend)
    _say(graph, "je veux parler à un humain")

    released = _say(graph, "reprendre")
    assert released["handled_by_human"] is False
    assert released["messages"][-1].content == TAKEOVER_RELEASED_MESSAGE

    assert entry_route(released) == "router"


def test_the_way_out_is_advertised_in_the_takeover_message() -> None:
    """An escape hatch nobody is told about is not an escape hatch."""
    assert "reprendre" in HUMAN_TAKEOVER_MESSAGE


def test_releasing_does_not_close_the_case(backend) -> None:
    """The advisor still owns the ticket; only the bot resumes answering."""
    graph = _graph(backend)
    _say(graph, "je veux parler à un humain")
    _say(graph, "reprendre")

    assert len(backend.list_tickets(USER)) == 1


def test_escalation_masks_pii_before_filing_the_case(backend) -> None:
    """A case is persisted: it must not carry a raw card number."""
    from support_agent.guardrails import build_tool_guard

    builder = StateGraph(SupportState, context_schema=AgentContext)
    builder.add_node("escalate", make_escalate(backend, build_tool_guard(2000, 5, 60.0)))
    builder.add_edge(START, "escalate")
    builder.add_edge("escalate", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    _say(graph, "un humain svp, ma carte 4111 1111 1111 1111 a été débitée deux fois")

    body = backend.list_tickets(USER)[0].body
    assert "4111 1111 1111 1111" not in body
    assert "débitée deux fois" in body
