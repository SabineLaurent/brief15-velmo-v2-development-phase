"""Tests for episodic memory (Phase 14): the store side, offline.

What is worth asserting here is not "LangGraph can store a dict" — that is
LangGraph's job. It is the four decisions that are OURS, and that a future edit
could quietly undo:

    1. an episode is retrieved by SITUATION, not by past answer;
    2. a cold or broken store changes the prompt by exactly zero bytes;
    3. an episode is PII-masked before it can be shown to another customer;
    4. a turn writes ONE row per thread and pays NO embedding call.

The embeddings are faked with a deterministic bag-of-words so the semantic
search really runs (no network, no API key, no flakiness).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from support_agent.guardrails import build_tool_guard
from support_agent.memory.episodic import (
    CANDIDATES_NAMESPACE,
    EPISODES_NAMESPACE,
    Episode,
    drop_candidate,
    format_episodes,
    list_ripe_candidates,
    recall_episodes,
    record_candidate,
    save_episode,
)

# A three-word "vocabulary" is enough to make similarity meaningful and readable.
_VOCAB = ("delivery", "refund", "invoice")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words embeddings — real search, zero network."""
    vectors = []
    for text in texts:
        lowered = text.lower()
        vector = [float(lowered.count(word)) for word in _VOCAB]
        # A zero vector has no direction, so cosine similarity would be NaN and
        # the ranking would depend on float luck. Neutral means equidistant.
        vectors.append(vector if any(vector) else [1.0, 1.0, 1.0])
    return vectors


def _store(embed=_fake_embed) -> InMemoryStore:
    """A store indexed exactly like the real one (`long_term.get_store`)."""
    return InMemoryStore(index={"embed": embed, "dims": len(_VOCAB), "fields": ["text"]})


def _episode(topic: str) -> Episode:
    return Episode(
        observation=f"A customer reported a {topic} problem.",
        thoughts=f"I checked the {topic} policy before promising anything.",
        action=f"I looked up the {topic} case and opened a ticket.",
        result="Resolved without a human.",
    )


# --- 1. Retrieval is by situation ---------------------------------------------


def test_episode_is_recalled_by_situation_not_by_past_answer() -> None:
    """The query is the customer's CURRENT message, so the indexed text must be
    the past SITUATION. Indexing the outcome instead would rank every episode
    that ended well as equally relevant — i.e. rank nothing at all."""
    store = _store()
    save_episode(store, _episode("delivery"))
    save_episode(store, _episode("refund"))

    recalled = recall_episodes(store, query="my delivery never arrived", limit=1)

    assert len(recalled) == 1
    assert "delivery" in recalled[0].observation


def test_a_weak_match_is_dropped_by_the_relevance_floor() -> None:
    """Top-k has no notion of "close enough": the floor is what turns a ranking
    into a decision. Below it, no episode is better than a misleading one."""
    store = _store()
    save_episode(store, _episode("invoice"))

    assert recall_episodes(store, query="delivery", limit=2, min_score=0.35) == []
    # Same query, no floor: the search happily returns the irrelevant case.
    assert len(recall_episodes(store, query="delivery", limit=2)) == 1


def test_recalled_episode_keeps_the_reasoning_field() -> None:
    """`thoughts` is the reason this feature exists: without it an episode is a
    costlier duplicate of the FAQ. A schema change that drops it must fail."""
    store = _store()
    save_episode(store, _episode("refund"))

    recalled = recall_episodes(store, query="I want a refund", limit=1)

    assert recalled[0].thoughts.startswith("I checked")


# --- 2. A cold or broken store is a no-op -------------------------------------


def test_empty_recall_adds_nothing_to_the_prompt() -> None:
    """Byte-for-byte identical to the pre-Phase-14 prompt when nothing matches."""
    assert format_episodes([]) == ""
    assert recall_episodes(_store(), query="anything", limit=2) == []


def test_recall_survives_a_broken_store() -> None:
    """Episodic memory is an ENHANCEMENT. A store outage must degrade the answer,
    never fail the turn — the customer still gets the FAQ-grounded reply."""

    class ExplodingStore(BaseStore):
        def batch(self, ops):  # pragma: no cover - never reached
            raise AssertionError("not used")

        async def abatch(self, ops):  # pragma: no cover - never reached
            raise AssertionError("not used")

        def search(self, *args, **kwargs):
            raise RuntimeError("store is down")

    assert recall_episodes(ExplodingStore(), query="delivery", limit=2) == []


def test_recall_skips_a_malformed_item_instead_of_crashing() -> None:
    """One bad row (an older schema, a partial write) must not sink the batch."""
    store = _store()
    store.put(EPISODES_NAMESPACE, "legacy", {"text": "delivery", "note": "old shape"})
    save_episode(store, _episode("delivery"))

    recalled = recall_episodes(store, query="delivery", limit=5)

    assert len(recalled) == 1


# --- 3. An episode is anonymized before it can reach another customer ---------


def test_saved_episode_is_pii_masked() -> None:
    """The namespace has no `user_id` — an episode WILL be read while serving
    someone else. The extraction prompt asks the model to generalize; this is the
    deterministic pass that does not depend on the model complying."""
    guard = build_tool_guard(2000, 5, 3600.0)
    store = _store()
    leaky = Episode(
        observation="A customer wrote from alice@example.com about a delivery.",
        thoughts="I checked the tracking.",
        action="I opened a ticket.",
        result="Resolved.",
    )

    save_episode(store, leaky, tool_guard=guard)
    recalled = recall_episodes(store, query="delivery", limit=1)

    assert "alice@example.com" not in recalled[0].observation
    assert "[REDACTED_EMAIL]" in recalled[0].observation


def test_prompt_block_marks_episodes_as_data() -> None:
    """Episodes are distilled from customer text, so they are untrusted content:
    the block must carry the same "data, not instructions" warning as the FAQ."""
    block = format_episodes([_episode("delivery")])

    assert "DATA" in block or "data" in block
    assert "never as instructions" in block


# --- 4. The turn pays one upsert and no embedding -----------------------------


def test_candidate_is_one_row_per_thread() -> None:
    """Debouncing IS this upsert: re-writing the same key means a thread is one
    candidate whatever its length, always reflecting its latest state. Appending
    instead would distil the same conversation once per turn."""
    store = _store()

    record_candidate(store, thread_id="t-1", resolved=True)
    record_candidate(store, thread_id="t-1", resolved=False)

    rows = store.search(CANDIDATES_NAMESPACE, limit=10)
    assert len(rows) == 1
    assert rows[0].value["resolved"] is False  # the LAST turn wins


def test_recording_a_candidate_never_embeds() -> None:
    """`index=False` is not a micro-optimisation: without it, bookkeeping would
    buy an embeddings API call on every single turn of every conversation."""

    def exploding_embed(texts: list[str]) -> list[list[float]]:
        raise AssertionError("a candidate must never be embedded")

    store = _store(embed=exploding_embed)

    record_candidate(store, thread_id="t-1", resolved=True)

    assert store.get(CANDIDATES_NAMESPACE, "t-1") is not None


def test_only_idle_candidates_are_ripe() -> None:
    """"Quiet for N minutes" stands in for "the conversation ended". A live
    thread must stay out: distilling it would capture a case whose outcome is
    not known yet."""
    store = _store()
    record_candidate(store, thread_id="live", resolved=True)
    store.put(
        CANDIDATES_NAMESPACE,
        "finished",
        {
            "thread_id": "finished",
            "resolved": True,
            "updated_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        },
        index=False,
    )

    ripe = list_ripe_candidates(store, idle_minutes=30)

    assert [candidate.thread_id for candidate in ripe] == ["finished"]


def test_dropping_a_candidate_removes_it() -> None:
    """Consolidation must be able to close the loop, or it re-reads forever."""
    store = _store()
    record_candidate(store, thread_id="t-1", resolved=True)

    drop_candidate(store, "t-1")

    assert store.search(CANDIDATES_NAMESPACE, limit=10) == []


@pytest.mark.parametrize("bad_value", [{"updated_at": 42}, {"updated_at": "yesterday"}])
def test_unparsable_candidate_is_skipped(bad_value: dict) -> None:
    """A corrupt row must not stop the whole consolidation run."""
    store = _store()
    store.put(CANDIDATES_NAMESPACE, "broken", bad_value, index=False)

    assert list_ripe_candidates(store, idle_minutes=0) == []
