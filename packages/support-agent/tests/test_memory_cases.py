"""Chantier 1: the starter's 12 memory cases, run as a spec.

`data/eval/memory_cases.jsonl` states R1/R2/R3/R5 as data — scripted
conversations plus what the agent must (or must not) be able to say afterwards.
It is executed here rather than paraphrased, the same way
`test_moderation.py` executes the guardrail corpus.

**Each tag is asserted against the mechanism that actually implements it**, which
is the one adaptation this port makes:

    R1  ->  the CHECKPOINTER, keyed by thread_id  (nothing is dropped, no search)
    R2  ->  the STORE,        keyed by user_id    (durable, survives the process)
    R3  ->  `memories_namespace`                  (one user cannot reach another)
    R5  ->  `forget_user_memories`                (deleted, and verified deleted)

In the starter all four go through one `MemoryManager` and R1 is a semantic
lookup; asserting R1 through a search here would test the store instead of the
fil, i.e. the wrong mechanism. Same reasoning as
`test_memory_requirements.py`, which holds the DEPTH of R1/R2 (30 turns,
recall by meaning against a competitor). This file adds the corpus's BREADTH:
ten more kinds of fact, and two pairs — the R3 pair and the R5 pair — that were
written to catch cross-contamination.

Offline by construction: no provider, no key, no network. The embeddings are a
deterministic bag-of-words over the corpus's own vocabulary, and the durable
store is a SQLite file under `tmp_path`.
"""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from support_agent.config import Settings
from support_agent.eval.corpus import (
    load_memory_cases,
    memory_assistant_turns,
    memory_user_turns,
)
from support_agent.memory import long_term
from support_agent.memory.long_term import get_store, memories_namespace
from support_agent.memory.privacy import (
    forget_user_memories,
    list_user_memories,
    search_user_memories,
)
from support_agent.memory.short_term import get_checkpointer

# R1's own number: the requirement is "hold a 30-turn conversation", and the
# corpus's scripted conversations are 2-3 turns long. Replaying them alone would
# assert that a graph can hold a list, so every R1 case is padded past the
# threshold before its recall is checked.
_R1_MIN_TURNS = 30

# The floor for the destructive R5 path. Passed explicitly, never defaulted: the
# production value is calibrated per embeddings model (`forget_min_score` in
# config.py), so a test that inherited it would pass or fail for reasons unrelated
# to the code under test.
_FLOOR = 0.5

# Vocabulary for the fake embeddings, taken from the corpus's own wording. Kept
# small and readable so a failure is diagnosable by eye.
_VOCAB = (
    "taille",
    "clubs",
    "revendeur",
    "commande",
    "prioritaire",
    "adresse",
    "email",
    "maillot",
    "postal",
)


class _FakeEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings — real semantic search, zero network."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)

    @staticmethod
    def _one(text: str) -> list[float]:
        lowered = text.lower()
        vector = [float(lowered.count(word)) for word in _VOCAB]
        # A zero vector has no direction: cosine similarity would be NaN and the
        # ranking would depend on float luck. Neutral means equidistant.
        return vector if any(vector) else [1.0] * len(_VOCAB)


def _settings(**overrides: object) -> Settings:
    """Settings from explicit values, ignoring the developer's own `.env`.

    `config.py` calls `load_dotenv()` at import, so the host `.env` is already in
    `os.environ` and constructor kwargs are the only way to be sure of what is
    under test.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _durable_store(tmp_path, monkeypatch):
    """A store built by the REAL factory, on a temp SQLite file, offline.

    `get_store` is the code under test — the backend switch, the index config and
    `setup()` all live there. Only the embeddings are faked, because the
    alternative is a network call and a credential.
    """
    monkeypatch.setattr(long_term, "get_embeddings", lambda _s: _FakeEmbeddings())
    return get_store(
        _settings(
            persistence_backend="sqlite",
            agent_memory_db_path=str(tmp_path / "memories.db"),
            memory_ttl_days=None,
        )
    )


def _remember_user_turns(store, case: dict) -> None:
    """Store what the customer stated, one fact per turn.

    The corpus hands us a transcript, not a list of facts; in production the model
    decides what is durable and calls `save_memory`. Extracting that decision here
    would put an LLM in an offline suite, so the customer's own sentences are
    stored verbatim — which is also the harder case for R5, since the fact to
    forget is buried in prose rather than sitting in a tidy field.
    """
    namespace = memories_namespace(case["user_id"])
    for index, text in enumerate(memory_user_turns(case)):
        store.put(namespace, f"{case['id']}-{index}", {"text": text})


# --- The corpus is the spec -------------------------------------------------


def test_the_corpus_is_the_size_and_shape_we_ported() -> None:
    """A changed corpus needs a look, not a silent pass."""
    cases = load_memory_cases()
    assert len(cases) == 12
    assert [len(load_memory_cases(tag)) for tag in ("R1", "R2", "R3", "R5")] == [4, 4, 2, 2]
    # Every case must be reachable by a tag: an untagged row would be collected,
    # never asserted, and look covered.
    assert sum(len(load_memory_cases(t)) for t in ("R1", "R2", "R3", "R5")) == len(cases)


def test_every_searched_fact_is_visible_to_the_fake_embeddings() -> None:
    """Guard against the corpus drifting out of `_VOCAB`.

    A fact with no vocabulary word gets the neutral vector, so it becomes
    equidistant from every query — the search would still "work" while measuring
    nothing. Without this check, adding a case could silently turn an assertion
    below into a coin flip.

    Scoped to the tags that SEARCH (R2/R3/R5). R1 lives in the checkpointer, which
    has no embeddings at all: including it would fail on sentences whose
    retrievability is irrelevant ("Commence par le Bresil.") and the honest fix
    would then be to pad `_VOCAB` with words no assertion uses.
    """
    blind = [
        text
        for tag in ("R2", "R3", "R5")
        for case in load_memory_cases(tag)
        for text in memory_user_turns(case)
        if not any(word in text.lower() for word in _VOCAB)
    ]
    assert not blind, f"no vocabulary word in: {blind}"


# --- R1: hold the fil of a 30-turn conversation ------------------------------


def _replay(case: dict, checkpointer, *, pad_to: int):
    """Replay a scripted conversation, then pad it past `pad_to` turns.

    Deliberately not `build_support_graph()`: what R1 rests on is the
    CHECKPOINTER carrying state from one `invoke` to the next on the same
    `thread_id`. The real graph would drag three LLM nodes and a vector store
    along, and the assertion would no longer be about persistence.
    """
    scripted = iter(memory_assistant_turns(case))

    def respond(state: MessagesState) -> dict:
        return {"messages": [AIMessage(content=next(scripted, "Tres bien."))]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": f"corpus-{case['id']}"}}
    messages = memory_user_turns(case)
    messages += [f"Question de suivi {i}." for i in range(pad_to - len(messages))]
    for text in messages:
        graph.invoke({"messages": [HumanMessage(content=text)]}, config)
    return graph, config, len(messages)


@pytest.mark.parametrize(
    "case", load_memory_cases("R1"), ids=[c["id"] for c in load_memory_cases("R1")]
)
def test_R1_the_first_turn_survives_a_long_conversation(case: dict) -> None:
    """R1: what the customer said early is still in the fil 30 turns later.

    Thirty-odd separate `invoke` calls, not one call with a long list — that is
    the difference between "the graph can hold a list" and "the agent remembers
    the discussion", which is what the requirement asks for.
    """
    checkpointer = get_checkpointer(_settings(persistence_backend="memory"))
    graph, config, turns = _replay(case, checkpointer, pad_to=_R1_MIN_TURNS)

    history = graph.get_state(config).values["messages"]
    # Each turn contributes the customer message + the agent reply.
    assert len(history) == 2 * turns
    assert turns >= _R1_MIN_TURNS

    expected = case["evaluation"]["expected_substring"]
    assert any(expected in str(m.content) for m in history), (
        f"[{case['id']}] {expected!r} fell out of a {turns}-turn fil"
    )


@pytest.mark.parametrize(
    "case", load_memory_cases("R1"), ids=[c["id"] for c in load_memory_cases("R1")]
)
def test_R1_a_long_conversation_resumes_on_a_new_graph_object(case: dict) -> None:
    """R1 across a restart: the checkpointer holds the fil, not the process.

    Rebuilding the graph over the same saver stands in for the process being
    restarted — the property `sqlite`/`postgres` then extend across real process
    boundaries (`test_persistence.py`).
    """
    checkpointer = get_checkpointer(_settings(persistence_backend="memory"))
    _, config, _ = _replay(case, checkpointer, pad_to=_R1_MIN_TURNS)

    def noop(state: MessagesState) -> dict:
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("noop", noop)
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    resumed = builder.compile(checkpointer=checkpointer).get_state(config).values["messages"]

    expected = case["evaluation"]["expected_substring"]
    assert any(expected in str(m.content) for m in resumed)


# --- R2: remember durable facts from one session to the next ------------------


@pytest.mark.parametrize(
    "case", load_memory_cases("R2"), ids=[c["id"] for c in load_memory_cases("R2")]
)
def test_R2_a_durable_fact_survives_into_a_new_session(
    case: dict, tmp_path, monkeypatch
) -> None:
    """R2: a second session, days later, finds what the first one learned.

    Two independent store objects over the same file — the second never sees the
    first, which is exactly the situation "the customer comes back next week"
    puts the agent in.

    The assertion is PRESENCE in the recalled set, not rank. Ranking under fake
    bag-of-words embeddings would measure the fake ("statut de compte" shares no
    word with "je suis revendeur", so a real model ranks it and this one cannot),
    and dressing that up would test the test. The rank property is asserted where
    it is fair — with a real competitor — in `test_memory_requirements.py`.
    """
    session1 = _durable_store(tmp_path, monkeypatch)
    _remember_user_turns(session1, case)

    session2 = _durable_store(tmp_path, monkeypatch)
    recalled = " ".join(
        record.text
        for record in search_user_memories(
            session2, case["user_id"], case["evaluation"]["question"], limit=10
        )
    )

    expected = case["evaluation"]["expected_substring"]
    assert expected in recalled, f"[{case['id']}] {expected!r} not recalled from {recalled!r}"


# --- R3: strict isolation between users --------------------------------------


def test_R3_neither_user_of_the_pair_can_reach_the_others_fact(
    tmp_path, monkeypatch
) -> None:
    """R3, on the pair the corpus built for it.

    The two cases are the same sentence with a different order number, stored for
    two different customers. Under bag-of-words embeddings the two facts have
    IDENTICAL vectors, so similarity cannot tell them apart — the only thing
    keeping them separate is `memories_namespace(user_id)`. That is what makes
    this pair worth executing rather than one isolation test with distinct text:
    it removes the possibility of passing by lexical luck.
    """
    store = _durable_store(tmp_path, monkeypatch)
    cases = load_memory_cases("R3")
    assert len(cases) == 2
    for case in cases:
        _remember_user_turns(store, case)

    for case in cases:
        other = next(c for c in cases if c["id"] != case["id"])
        mine = case["evaluation"]["expected_substring"]
        theirs = other["evaluation"]["expected_substring"]

        recalled = " ".join(
            record.text
            for record in search_user_memories(
                store, case["user_id"], case["evaluation"]["question"], limit=10
            )
        )
        assert mine in recalled, f"[{case['id']}] lost its own fact"
        assert theirs not in recalled, f"[{case['id']}] LEAKED {theirs!r} from {other['user_id']}"

        # And the audit dump — the other way in, and the one an operator uses.
        dumped = " ".join(r.text for r in list_user_memories(store, case["user_id"]))
        assert mine in dumped and theirs not in dumped


# --- R5: the right to be forgotten ------------------------------------------


@pytest.mark.parametrize(
    "case", load_memory_cases("R5"), ids=[c["id"] for c in load_memory_cases("R5")]
)
def test_R5_the_forgotten_fact_is_gone_and_verifiably_so(
    case: dict, tmp_path, monkeypatch
) -> None:
    """R5: "oublie mon adresse" deletes it, and the deletion is checkable.

    Three ways of looking, because a deletion that only holds for one of them is
    not a deletion: the semantic recall the agent uses, the audit dump an operator
    uses, and the return value that says what was destroyed.

    `forget_user_memories` re-reads the keys after deleting them and raises if any
    survive, so a silently swallowed write fails here loudly rather than being
    reported as a successful erasure.
    """
    store = _durable_store(tmp_path, monkeypatch)
    _remember_user_turns(store, case)
    user_id = case["user_id"]
    forbidden = case["evaluation"]["forbidden_substring"]

    # It really was there — otherwise the assertions below pass on an empty store.
    assert any(forbidden in r.text for r in list_user_memories(store, user_id))

    deleted = forget_user_memories(
        store, user_id, case["evaluation"]["target"], min_score=_FLOOR
    )

    assert deleted, f"[{case['id']}] nothing matched {case['evaluation']['target']!r}"
    assert any(forbidden in r.text for r in deleted), "deleted the wrong fact"
    assert not any(forbidden in r.text for r in list_user_memories(store, user_id))
    recalled = search_user_memories(store, user_id, case["evaluation"]["question"], limit=10)
    assert not any(forbidden in r.text for r in recalled)


@pytest.mark.parametrize(
    "case", load_memory_cases("R5"), ids=[c["id"] for c in load_memory_cases("R5")]
)
def test_R5_the_erasure_does_not_outlive_the_process(
    case: dict, tmp_path, monkeypatch
) -> None:
    """The half of R5 a same-process assertion cannot see: it hit the DISK.

    Since Phase 10 the store is a durable SQLite file, so "deleted" must mean
    deleted in the file — an in-process cache that merely stopped returning the
    row would satisfy the test above and hand the fact back after a restart.
    """
    session1 = _durable_store(tmp_path, monkeypatch)
    _remember_user_turns(session1, case)
    forget_user_memories(
        session1, case["user_id"], case["evaluation"]["target"], min_score=_FLOOR
    )

    session2 = _durable_store(tmp_path, monkeypatch)
    survivors = list_user_memories(session2, case["user_id"])

    forbidden = case["evaluation"]["forbidden_substring"]
    assert not any(forbidden in r.text for r in survivors)
