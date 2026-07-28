"""Episodic memory in the graph (Phase 14-B/C/D): the loop, end to end, offline.

`test_episodic_memory.py` covers the store side. This file covers the two places
where episodic memory touches the running agent, plus the offline job that joins
them:

    read  ->  the support prompt grows a few-shot block, AFTER the stable part
    write ->  `close_turn` flags the thread, with no model and no embedding
    join  ->  `consolidate` turns quiet, RESOLVED threads into episodes

No provider key, no network: the chat model and the compiled graph are stubs,
because what is under test is our wiring, not LangChain's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore

from support_agent.config import Settings
from support_agent.graph.nodes import (
    SUPPORT_SYSTEM_PROMPT,
    EpisodicRecall,
    make_close_turn,
    make_support_model,
)
from support_agent.memory import consolidate as consolidate_module
from support_agent.memory.episodic import (
    CANDIDATES_NAMESPACE,
    EPISODES_NAMESPACE,
    Episode,
    save_episode,
)

_VOCAB = ("delivery", "refund", "invoice")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        lowered = text.lower()
        vector = [float(lowered.count(word)) for word in _VOCAB]
        vectors.append(vector if any(vector) else [1.0, 1.0, 1.0])
    return vectors


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(
        index={"embed": _fake_embed, "dims": len(_VOCAB), "fields": ["text"]}
    )


def _settings(**overrides: object) -> Settings:
    """Explicit values only — never the developer's own `.env` (see test_persistence)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _episode(topic: str = "delivery") -> Episode:
    return Episode(
        observation=f"A customer reported a {topic} problem.",
        thoughts=f"I checked the {topic} policy before promising anything.",
        action="I looked up the case and opened a ticket.",
        result="Resolved without a human.",
    )


def _stale_candidate(store: InMemoryStore, thread_id: str, *, resolved: bool) -> None:
    """A candidate old enough to be ripe, written explicitly.

    Written by hand rather than via `record_candidate` + a zero idle window: a
    freshly-written row is only microseconds old, so "is it ripe?" would depend
    on clock resolution. Tests must assert a decision, not a race.
    """
    store.put(
        CANDIDATES_NAMESPACE,
        thread_id,
        {
            "thread_id": thread_id,
            "resolved": resolved,
            "updated_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        },
        index=False,
    )


# --- Read side: the prompt ----------------------------------------------------


class _SpyModel:
    """A chat model that answers nothing and remembers the prompt it was given."""

    def __init__(self) -> None:
        self.seen: list = []

    def bind_tools(self, tools):  # noqa: ANN001 - stub
        return self

    def invoke(self, messages):  # noqa: ANN001 - stub
        self.seen = messages
        return AIMessage(content="ok")


def _system_prompt_seen(store: InMemoryStore, question: str) -> str:
    model = _SpyModel()
    node = make_support_model(
        model, [], (), EpisodicRecall(store, limit=2, min_score=0.35)
    )

    node({"messages": [HumanMessage(content=question)]})

    system = model.seen[0]
    assert isinstance(system, SystemMessage)
    return str(system.content)


def test_cold_store_leaves_the_support_prompt_byte_for_byte_identical(
    store: InMemoryStore,
) -> None:
    """The day episodic memory is empty (day one, and after every purge) the
    agent must behave EXACTLY as it did before Phase 14 — same prompt, same
    cache key, same tokens billed."""
    assert _system_prompt_seen(store, "where is my delivery?") == SUPPORT_SYSTEM_PROMPT


def test_a_matching_case_is_appended_after_the_stable_prompt(
    store: InMemoryStore,
) -> None:
    """Order is not cosmetic: the stable instructions must stay a PREFIX, or the
    provider's prompt cache is invalidated on every single turn."""
    save_episode(store, _episode("delivery"))

    prompt = _system_prompt_seen(store, "my delivery never arrived")

    assert prompt.startswith(SUPPORT_SYSTEM_PROMPT)
    assert "I checked the delivery policy" in prompt


def test_the_only_episode_in_store_is_not_injected_into_every_conversation(
    store: InMemoryStore,
) -> None:
    """A vector search always returns its top match — it cannot say "nothing here
    fits". Without the relevance floor, the FIRST episode ever written would be
    pasted into every conversation, and the agent would look like it is recalling
    when it is only repeating itself. This test failed before `min_score` existed."""
    save_episode(store, _episode("invoice"))

    prompt = _system_prompt_seen(store, "where is my delivery")

    assert prompt == SUPPORT_SYSTEM_PROMPT


def test_recall_is_disabled_by_passing_none(store: InMemoryStore) -> None:
    """The kill switch has to be provable at the node, not only in the builder."""
    save_episode(store, _episode("delivery"))
    model = _SpyModel()
    node = make_support_model(model, [], (), None)

    node({"messages": [HumanMessage(content="my delivery never arrived")]})

    assert str(model.seen[0].content) == SUPPORT_SYSTEM_PROMPT


# --- Write side: `close_turn` -------------------------------------------------

_CONFIG = {"configurable": {"thread_id": "t-1"}}


def test_only_the_support_branch_produces_a_candidate(store: InMemoryStore) -> None:
    """"Bonjour" is not a case. Distilling small talk would burn prompt space
    forever on an episode that teaches nothing."""
    close_turn = make_close_turn(store)

    close_turn({"messages": [], "route": "answer"}, _CONFIG)

    assert store.search(CANDIDATES_NAMESPACE, limit=10) == []


def test_a_support_turn_produces_a_resolved_candidate(store: InMemoryStore) -> None:
    close_turn = make_close_turn(store)

    close_turn({"messages": [], "route": "support"}, _CONFIG)

    rows = store.search(CANDIDATES_NAMESPACE, limit=10)
    assert len(rows) == 1
    assert rows[0].value["resolved"] is True


def test_a_handoff_flips_the_candidate_to_unresolved(store: InMemoryStore) -> None:
    """The escalation flag IS the outcome signal. On a takeover turn `route` still
    holds the PREVIOUS turn's value from the checkpoint, so the handoff check must
    win — otherwise a failed case would be recorded as a success and taught."""
    close_turn = make_close_turn(store)
    close_turn({"messages": [], "route": "support"}, _CONFIG)

    close_turn({"messages": [], "route": "support", "handled_by_human": True}, _CONFIG)

    rows = store.search(CANDIDATES_NAMESPACE, limit=10)
    assert len(rows) == 1  # still ONE row: same thread, same key
    assert rows[0].value["resolved"] is False


def test_no_thread_id_means_nothing_to_learn_from(store: InMemoryStore) -> None:
    """A bare `invoke` with no thread has no conversation to come back to."""
    make_close_turn(store)({"messages": [], "route": "support"}, {"configurable": {}})

    assert store.search(CANDIDATES_NAMESPACE, limit=10) == []


# --- The join: `consolidate` --------------------------------------------------


class _FakeGraph:
    """Stands in for the compiled graph: only `get_state` is ever called."""

    def __init__(self, threads: dict[str, list]) -> None:
        self._threads = threads

    def get_state(self, config):  # noqa: ANN001 - stub
        thread_id = config["configurable"]["thread_id"]
        return SimpleNamespace(values={"messages": self._threads[thread_id]})


def _thread(turns: int = 2) -> list:
    messages: list = []
    for _ in range(turns):
        messages.append(HumanMessage(content="my delivery never arrived"))
        messages.append(AIMessage(content="I checked the tracking for you."))
    return messages


def _run(store, graph, *, write: bool, model=None, monkeypatch=None):
    if model is not None:
        monkeypatch.setattr(consolidate_module, "get_chat_model", lambda _s: model)
    return consolidate_module.consolidate(
        write=write,
        settings=_settings(episodic_idle_minutes=30.0),
        graph=graph,
        store=store,
    )


class _StructuredModel:
    """A model whose structured output is a fixed episode (or an outage)."""

    def __init__(self, episode: Episode | None = None) -> None:
        self._episode = episode
        self.calls = 0

    def with_structured_output(self, schema):  # noqa: ANN001 - stub
        return self

    def invoke(self, messages):  # noqa: ANN001 - stub
        self.calls += 1
        if self._episode is None:
            raise RuntimeError("provider is down")
        return self._episode


def test_dry_run_reports_without_spending_a_single_llm_call(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default mode. You must be able to see what a learning loop is about
    to learn — on a machine with no provider key configured at all."""

    def explode(_settings):  # noqa: ANN001 - stub
        raise AssertionError("a dry run must not build a model")

    monkeypatch.setattr(consolidate_module, "get_chat_model", explode)
    _stale_candidate(store, "t-1", resolved=True)

    report = _run(store, _FakeGraph({"t-1": _thread()}), write=False)

    assert report.distilled == 1
    assert store.search(EPISODES_NAMESPACE, limit=10) == []  # nothing written
    assert len(store.search(CANDIDATES_NAMESPACE, limit=10)) == 1  # nothing consumed


def test_a_resolved_thread_becomes_an_episode(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_candidate(store, "t-1", resolved=True)
    model = _StructuredModel(_episode("delivery"))

    report = _run(
        store, _FakeGraph({"t-1": _thread()}), write=True, model=model, monkeypatch=monkeypatch
    )

    assert report.distilled == 1
    assert len(store.search(EPISODES_NAMESPACE, limit=10)) == 1
    # The candidate is consumed, so a second run cannot duplicate the episode.
    assert store.search(CANDIDATES_NAMESPACE, limit=10) == []


def test_an_escalated_thread_is_dropped_never_taught(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE quality gate. A memory that stores its failures poisons the pool it
    draws few-shot examples from, and it does so invisibly."""
    _stale_candidate(store, "t-1", resolved=False)
    model = _StructuredModel(_episode())

    report = _run(
        store, _FakeGraph({"t-1": _thread()}), write=True, model=model, monkeypatch=monkeypatch
    )

    assert report.skipped_unresolved == 1
    assert model.calls == 0  # not even read: no LLM spent on a failed case
    assert store.search(EPISODES_NAMESPACE, limit=10) == []
    assert store.search(CANDIDATES_NAMESPACE, limit=10) == []


def test_a_one_exchange_thread_is_not_a_case(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single question the FAQ answered outright is already covered — better —
    by the FAQ itself."""
    _stale_candidate(store, "t-1", resolved=True)
    short = [HumanMessage(content="hello?")]

    report = _run(
        store,
        _FakeGraph({"t-1": short}),
        write=True,
        model=_StructuredModel(_episode()),
        monkeypatch=monkeypatch,
    )

    assert report.skipped_too_short == 1
    assert store.search(EPISODES_NAMESPACE, limit=10) == []


def test_a_provider_outage_leaves_the_candidate_for_the_next_run(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Later", not "lost forever". The candidate row IS the retry queue — which
    is the reason this job reads the durable store instead of an in-RAM queue."""
    _stale_candidate(store, "t-1", resolved=True)

    report = _run(
        store,
        _FakeGraph({"t-1": _thread()}),
        write=True,
        model=_StructuredModel(None),  # provider down
        monkeypatch=monkeypatch,
    )

    assert report.failed == 1
    assert len(store.search(CANDIDATES_NAMESPACE, limit=10)) == 1


def test_a_live_thread_is_left_alone(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence is the only end-of-case signal a chat gives. Distilling a thread
    that is still going would record a case whose outcome is not known yet."""
    consolidate_module.consolidate  # noqa: B018 - readability
    store.put(
        CANDIDATES_NAMESPACE,
        "live",
        {
            "thread_id": "live",
            "resolved": True,
            "updated_at": datetime.now(UTC).isoformat(),
        },
        index=False,
    )

    report = _run(store, _FakeGraph({"live": _thread()}), write=False)

    assert report.examined == 0
