"""Tests for the right to be forgotten (R5) and traceability (R6).

What is worth asserting here is not "the store can delete a row" — that is
LangGraph's job. It is the five decisions that are OURS, and that a future edit
could quietly undo, each of which turns a GDPR feature into a lie if it breaks:

    1. an audit dump is COMPLETE (it pages; it does not stop at the store default);
    2. one customer's erasure never touches another's data;
    3. a deletion is VERIFIED by reading back, and fails loudly when it is not;
    4. a WEAK semantic match is never deleted — guessing destroys the wrong fact;
    5. "nothing matched" is reported as such, never as a successful deletion.

The embeddings are faked with a deterministic bag-of-words so the semantic search
really runs (no network, no API key, no flakiness).
"""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from support_agent.memory.long_term import memories_namespace
from support_agent.memory.memory_tools import build_memory_tools
from support_agent.memory.privacy import (
    delete_user_memories,
    erase_user_memories,
    forget_user_memories,
    list_user_memories,
    search_user_memories,
)

# The floor these tests exercise. Passed EXPLICITLY everywhere below rather than
# relying on a default: the production value is calibrated per embeddings model
# (`forget_min_score` in config.py), so a test that inherited it would start
# passing or failing for reasons that have nothing to do with the code under test.
_FLOOR = 0.5

# Three words are enough to make similarity meaningful AND readable in a failure.
_VOCAB = ("order", "address", "language")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words embeddings — real search, zero network."""
    vectors = []
    for text in texts:
        lowered = text.lower()
        vector = [float(lowered.count(word)) for word in _VOCAB]
        # A zero vector has no direction: cosine similarity would be NaN and the
        # ranking would depend on float luck. Neutral means equidistant.
        vectors.append(vector if any(vector) else [1.0, 1.0, 1.0])
    return vectors


def _store() -> InMemoryStore:
    """A store indexed exactly like the real one (`long_term.get_store`)."""
    return InMemoryStore(index={"embed": _fake_embed, "dims": len(_VOCAB), "fields": ["text"]})


def _remember(store: InMemoryStore, user_id: str, key: str, text: str) -> None:
    store.put(memories_namespace(user_id), key, {"text": text})


# --- R6: the audit dump -----------------------------------------------------


def test_audit_dump_lists_every_memory_not_just_the_first_page():
    """R6 is a COMPLETENESS requirement, so the paging is the thing under test.

    `BaseStore.search` defaults to 10 items. A dump that took that default would
    answer "here is everything I know about you" with the first ten rows and look
    perfectly correct while doing so.
    """
    store = _store()
    for index in range(250):
        _remember(store, "alice", f"k{index}", f"Fact number {index} about the order")

    records = list_user_memories(store, "alice")

    assert len(records) == 250
    assert len({r.key for r in records}) == 250


def test_audit_dump_carries_the_write_timestamps():
    """The 'traçabilité des écritures' half of R6: WHEN a fact was learned.

    We keep no write journal of our own — the store already timestamps every row,
    and a second copy would be a second thing to keep in sync (and to forget).
    """
    store = _store()
    _remember(store, "alice", "k1", "Prefers French for every order")

    (record,) = list_user_memories(store, "alice")

    assert record.created_at is not None
    assert record.updated_at is not None
    # The rendering is what an operator actually reads, so it is part of the API.
    assert "k1" in str(record) and "Prefers French" in str(record)


def test_audit_dump_survives_a_malformed_row():
    """A hand-edited or legacy row must still be visible — and thus deletable.

    An audit tool that hid the rows it could not parse would hide exactly the rows
    worth looking at.
    """
    store = _store()
    store.put(memories_namespace("alice"), "broken", {"note": "no text field"})

    (record,) = list_user_memories(store, "alice")

    assert record.key == "broken"
    assert "no text field" in record.text


# --- R3 + R5: erasure stays inside one customer -----------------------------


def test_erasure_never_crosses_users():
    """R3 under the most dangerous operation there is: a full art. 17 erasure."""
    store = _store()
    _remember(store, "alice", "a1", "Alice order 111")
    _remember(store, "alice", "a2", "Alice address is somewhere")
    _remember(store, "bob", "b1", "Bob order 222")

    erased = erase_user_memories(store, "alice")

    assert len(erased) == 2
    assert list_user_memories(store, "alice") == []
    assert [r.text for r in list_user_memories(store, "bob")] == ["Bob order 222"]


def test_erasure_returns_what_it_deleted():
    """R5 says 'vérifiable'. A function returning None proves nothing happened."""
    store = _store()
    _remember(store, "alice", "a1", "Order number 12345")

    erased = erase_user_memories(store, "alice")

    assert [r.text for r in erased] == ["Order number 12345"]


# --- R5: the deletion is verified, and fails loudly ------------------------


def test_deletion_is_verified_by_reading_back():
    store = _store()
    _remember(store, "alice", "a1", "Order number 12345")

    assert delete_user_memories(store, "alice", ["a1"]) == ["a1"]
    assert store.get(memories_namespace("alice"), "a1") is None


def test_a_deletion_that_did_not_happen_raises_instead_of_reporting_success():
    """The whole point of the read-back: a silent no-op must not look like success.

    A backend that swallowed the write, a stale cache or a namespace typo would
    all be invisible without this check — and the customer would be told their
    data is gone while it sits in the database.
    """

    class DeafStore(InMemoryStore):
        def delete(self, namespace, key):  # noqa: D102 - test double
            pass  # accepts the order, does nothing

    store = DeafStore(index={"embed": _fake_embed, "dims": len(_VOCAB), "fields": ["text"]})
    _remember(store, "alice", "a1", "Order number 12345")

    with pytest.raises(RuntimeError, match="could not be confirmed"):
        delete_user_memories(store, "alice", ["a1"])


# --- R5: targeted forget, and the similarity floor -------------------------


def test_targeted_forget_deletes_the_matching_fact_only():
    """'Oublie mon numéro de commande' must not take the rest with it."""
    store = _store()
    _remember(store, "alice", "a1", "The order reference is 12345")
    _remember(store, "alice", "a2", "Prefers to be addressed informally")

    deleted = forget_user_memories(store, "alice", "order", min_score=_FLOOR)

    assert [r.key for r in deleted] == ["a1"]
    assert [r.key for r in list_user_memories(store, "alice")] == ["a2"]


def test_a_weak_match_is_never_deleted():
    """The floor is the feature: acting on a weak match destroys the WRONG fact.

    Recall can afford a bad match (wasted tokens). Deletion cannot — it is
    irreversible and done on the customer's behalf.
    """
    store = _store()
    _remember(store, "alice", "a1", "The order reference is 12345")

    # Nothing stored is about a language preference. The closest row still comes
    # back from the vector search — it always does — but below the floor.
    deleted = forget_user_memories(store, "alice", "language", min_score=_FLOOR)

    assert deleted == []
    assert len(list_user_memories(store, "alice")) == 1
    # Guard the premise of this test: if the fake embeddings ever made this a
    # strong match, the assertion above would pass for the wrong reason.
    (match,) = search_user_memories(store, "alice", "language", limit=1)
    assert match.score is not None and match.score < _FLOOR


def test_forget_refuses_to_guess_when_the_store_has_no_vector_index():
    """No index means no score, so there is no similarity to judge — delete nothing."""
    store = InMemoryStore()  # deliberately unindexed
    _remember(store, "alice", "a1", "The order reference is 12345")

    assert forget_user_memories(store, "alice", "order", min_score=_FLOOR) == []
    assert len(list_user_memories(store, "alice")) == 1


# --- R5 from inside the conversation: the agent-facing tool ----------------


def _forget_tool():
    tools = {
        tool.name: tool for tool in build_memory_tools(forget_min_score=_FLOOR)
    }
    return tools["forget_memory"]


def test_the_agent_gets_a_forget_tool_at_all():
    assert {"save_memory", "search_memories", "forget_memory"} <= {
        tool.name for tool in build_memory_tools()
    }


def test_forget_tool_tells_the_model_when_nothing_matched():
    """R5's honesty clause: the model must not confirm a deletion that never was.

    The tool's return string is the only thing the model sees, so the distinction
    between "deleted" and "nothing matched" has to be unmistakable in the text.
    """
    store = _store()
    _remember(store, "alice", "a1", "The order reference is 12345")
    tool = _forget_tool()

    result = tool.invoke(
        {"what": "language", "runtime": _runtime(store, "alice")},
    )

    assert "nothing was deleted" in result.lower()
    assert len(list_user_memories(store, "alice")) == 1


def test_forget_tool_reports_exactly_what_it_deleted():
    store = _store()
    _remember(store, "alice", "a1", "The order reference is 12345")
    tool = _forget_tool()

    result = tool.invoke({"what": "order", "runtime": _runtime(store, "alice")})

    assert "The order reference is 12345" in result
    assert list_user_memories(store, "alice") == []


def _runtime(store, user_id: str):
    """A `ToolRuntime` the tool can read. Only `.store` and `.context` are used.

    The remaining fields are required by the constructor, not by our tools — we
    pass real Nones rather than a mock, so this breaks loudly if a future version
    of the tools starts depending on the graph state or the stream writer.
    """
    from langchain.tools import ToolRuntime

    from support_agent.memory.long_term import AgentContext

    return ToolRuntime(
        context=AgentContext(user_id=user_id),
        store=store,
        state=None,
        config=None,
        stream_writer=None,
        tool_call_id=None,
    )
