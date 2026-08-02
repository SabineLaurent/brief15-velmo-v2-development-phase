"""R1 and R2 of the memory spec, asserted against this project's real seams.

    R1  ->  hold a 30-turn conversation
    R2  ->  remember durable facts from one SESSION to the next, days later

They are asserted against two different mechanisms on purpose, because that is how they
are implemented here:

    R1  ->  the CHECKPOINTER, keyed by thread_id  (nothing dropped, no search)
    R2  ->  the STORE,        keyed by user_id    (durable, semantic)

Asserting R1 through a semantic search would test the wrong one.

Offline by construction: no provider, no server, no API key. The embeddings are a
deterministic bag-of-words and the durable store is a SQLite file in `tmp_path`, so this
suite keeps running on a laptop with no credentials — which is the only kind of suite
that keeps being run.
"""

from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from support_agent.config import Settings
from support_agent.memory import long_term
from support_agent.memory.long_term import get_store, memories_namespace
from support_agent.memory.short_term import get_checkpointer

_VOCAB = ("pointure", "club", "revendeur", "commande", "adresse")


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
        return vector if any(vector) else [1.0] * len(_VOCAB)


def _settings(**overrides: object) -> Settings:
    """Settings from explicit values, ignoring the developer's own `.env`.

    Same trick as `test_persistence.py`: `config.py` calls `load_dotenv()` at
    import, so the host `.env` is already in `os.environ` and constructor kwargs
    are the only way to be sure of what is under test.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# --- R1: hold a 30-turn conversation ----------------------------------------


def _echo_graph(checkpointer):
    """The smallest graph that has a conversation: one node that replies.

    Deliberately not `build_support_graph()`. What R1 rests on is the
    CHECKPOINTER carrying state from one `invoke` to the next on the same
    `thread_id` — bringing the real graph in would drag three LLM nodes and a
    vector store along, and the assertion would no longer be about persistence.
    """

    def respond(state: MessagesState) -> dict:
        last = state["messages"][-1].content
        return {"messages": [AIMessage(content=f"ack: {last}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


def test_the_first_turn_is_still_there_after_30_turns() -> None:
    """R1: information given on turn 1 survives 30 more turns, verbatim.

    Thirty-one separate `invoke` calls, not one call with a long list — that is
    the difference between "the graph can hold a list" and "the agent remembers
    the discussion", which is the requirement.
    """
    graph = _echo_graph(get_checkpointer(_settings(persistence_backend="memory")))
    config = {"configurable": {"thread_id": "acc-recall"}}

    graph.invoke(
        {"messages": [HumanMessage(content="Ma commande prioritaire est O-2024-0101.")]},
        config,
    )
    for index in range(30):
        graph.invoke(
            {"messages": [HumanMessage(content=f"Question de suivi {index}.")]}, config
        )

    history = graph.get_state(config).values["messages"]
    assert len(history) == 62
    assert "O-2024-0101" in history[0].content


def test_one_conversation_never_leaks_into_another() -> None:
    """The other half of R1: `thread_id` isolates conversations, not just users.

    Two threads of the SAME customer must not see each other — otherwise
    "remembering the discussion" would mean remembering all of them at once.
    """
    graph = _echo_graph(get_checkpointer(_settings(persistence_backend="memory")))

    graph.invoke(
        {"messages": [HumanMessage(content="Ma commande est O-2024-0101.")]},
        {"configurable": {"thread_id": "thread-a"}},
    )
    other = graph.invoke(
        {"messages": [HumanMessage(content="Bonjour.")]},
        {"configurable": {"thread_id": "thread-b"}},
    )

    assert not any("O-2024-0101" in str(m.content) for m in other["messages"])


def test_a_thread_resumes_on_a_brand_new_graph_object() -> None:
    """R1 across a restart: the durable checkpointer, not the process, holds the fil.

    Rebuilding the graph stands in for the process being restarted. With the
    in-memory backend this asserts the saver is what carries the state, not the
    compiled graph — the same property `sqlite`/`postgres` then extend across
    process boundaries.
    """
    checkpointer = get_checkpointer(_settings(persistence_backend="memory"))
    config = {"configurable": {"thread_id": "acc-resume"}}

    _echo_graph(checkpointer).invoke(
        {"messages": [HumanMessage(content="Je suis revendeur.")]}, config
    )
    resumed = _echo_graph(checkpointer).get_state(config).values["messages"]

    assert "revendeur" in resumed[0].content


# --- R2: remember durable facts from one session to the next -----------------


def _durable_store(tmp_path, monkeypatch):
    """A store built by the REAL factory, on a temp SQLite file, offline.

    `get_store` is the code under test — it is where the backend switch, the
    index config and `setup()` live. Only the embeddings are faked, because the
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


def test_facts_survive_into_a_new_session(tmp_path, monkeypatch) -> None:
    """R2: a second session, days later, finds what the first one learned.

    Two independent store objects over the same file. The second one never sees
    the first — which is exactly the situation "the customer comes back next
    week" puts the agent in.
    """
    session1 = _durable_store(tmp_path, monkeypatch)
    namespace = memories_namespace("acc-marc")
    session1.put(namespace, "f1", {"text": "Sa pointure est L"})
    session1.put(namespace, "f2", {"text": "Supporte l'OM, club de coeur"})
    session1.put(namespace, "f3", {"text": "Segment: revendeur professionnel"})

    session2 = _durable_store(tmp_path, monkeypatch)
    recalled = " ".join(
        item.value["text"]
        for item in session2.search(namespace, query="pointure club revendeur", limit=5)
    )

    assert "L" in recalled
    assert "OM" in recalled
    assert "revendeur" in recalled


def test_a_new_session_recalls_by_MEANING_not_by_keyword(tmp_path, monkeypatch) -> None:
    """R2 is a *semantic* recall: the customer will not repeat their own wording.

    Asserted because a store that happened to work by exact match would pass the
    test above while failing every real conversation.
    """
    store = _durable_store(tmp_path, monkeypatch)
    namespace = memories_namespace("acc-marc")
    store.put(namespace, "f1", {"text": "Sa pointure est L"})
    store.put(namespace, "f2", {"text": "Son adresse est 12 rue des Lilas"})

    best = store.search(namespace, query="quelle taille porte-t-il", limit=1)

    assert best and best[0].key == "f1"


def test_the_in_memory_backend_deliberately_does_NOT_persist(monkeypatch) -> None:
    """The boundary of R2, made explicit: `memory` is a dev convenience.

    Worth an assertion rather than a comment, because it is the failure people
    hit and misread — "the agent forgot everything" is the CONFIGURED behaviour
    of the default backend, not a bug in the memory layer.
    """
    monkeypatch.setattr(long_term, "get_embeddings", lambda _s: _FakeEmbeddings())
    settings = _settings(persistence_backend="memory")
    namespace = memories_namespace("acc-marc")

    first = get_store(settings)
    first.put(namespace, "f1", {"text": "Sa pointure est L"})
    second = get_store(settings)

    assert first.search(namespace, query="pointure", limit=1)
    assert second.search(namespace, query="pointure", limit=1) == []
