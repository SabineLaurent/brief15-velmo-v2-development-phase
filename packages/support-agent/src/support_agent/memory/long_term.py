"""Long-term (cross-session) memory: the Store factory + runtime context.

Where the *checkpointer* (short-term) remembers ONE conversation, keyed by
`thread_id`, the *Store* (long-term) remembers a USER across conversations,
keyed by `user_id`. Different question, different tool:

    checkpointer  ->  "what did we say earlier in THIS chat?"   (thread_id)
    store         ->  "what do I know about THIS customer?"     (user_id)

The store organizes data by hierarchical *namespaces* (tuples), so one
customer's memories are physically separated from another's.

For now we use an in-memory store: perfect for learning, but state is lost when
the process exits. Swapping to a durable backend (SQLite / Postgres) later is a
one-line change *here* — the agent code never changes. Same agnostic idea as
the LLM and checkpointer factories.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from support_agent.config import Settings, get_settings
from support_agent.llm.embeddings import get_embeddings
from support_agent.memory.sqlite_conn import open_sqlite_connection


@dataclass
class AgentContext:
    """Runtime context passed at invoke time: identifies WHO we are talking to.

    This is how the agent's memory tools know under which `user_id` to read and
    write, so long-term memory stays scoped per customer.
    """

    user_id: str


def get_store(settings: Settings | None = None) -> BaseStore:
    """Return the configured long-term memory store.

    The store always keeps *semantic search* over memories — we reuse the same
    agnostic embeddings as the FAQ (Phase 4), so recall works by meaning, not
    exact keywords. The storage backend is a config choice (`PERSISTENCE_BACKEND`),
    exactly like the checkpointer:

        "memory"  ->  InMemoryStore: lost when the process exits
        "sqlite"  ->  SqliteStore:   durable on disk, survives a restart

    Args:
        settings: Optional settings override (handy for tests).
    """
    settings = settings or get_settings()
    embeddings = get_embeddings(settings)
    # Probe once to learn the vector size instead of hard-coding a per-model
    # dimension — keeps the store provider-agnostic like everything else.
    dims = len(embeddings.embed_query("probe"))
    index = {"embed": embeddings, "dims": dims, "fields": ["text"]}

    backend = settings.persistence_backend.lower()

    if backend == "memory":
        return InMemoryStore(index=index)

    if backend == "sqlite":
        from langgraph.store.sqlite import SqliteStore

        # Same self-managed connection as the checkpointer; `setup()` creates the
        # store tables (and the vector index, via the bundled sqlite-vec) on first
        # use. The semantic `index` config is identical to the in-memory store.
        store = SqliteStore(open_sqlite_connection(settings.agent_memory_db_path), index=index)
        store.setup()
        return store

    if backend == "postgres":
        # Production drop-in: `pip install langgraph-checkpoint-postgres`, then
        #   from langgraph.store.postgres import PostgresStore
        #   store = PostgresStore.from_conn_string(settings.database_url) / pool
        #   store.setup()
        raise NotImplementedError(
            "Postgres store not wired yet. Add a DATABASE_URL setting and build a "
            "PostgresStore here (see docstring)."
        )

    raise ValueError(
        f"Unknown PERSISTENCE_BACKEND={settings.persistence_backend!r}. "
        f"Expected one of: memory, sqlite, postgres."
    )
