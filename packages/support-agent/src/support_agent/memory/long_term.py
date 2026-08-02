"""Long-term (cross-session) memory: the Store factory + runtime context.

    checkpointer  ->  "what did we say earlier in THIS chat?"   (thread_id)
    store         ->  "what do I know about THIS customer?"     (user_id)

The store organizes data by hierarchical namespaces (tuples), so one customer's memories
are physically separated from another's. The backend is a config choice
(`PERSISTENCE_BACKEND`), same agnostic idea as the LLM and checkpointer factories;
semantic search is preserved whichever engine is underneath.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.store.base import BaseStore, TTLConfig
from langgraph.store.memory import InMemoryStore

from support_agent.config import Settings, get_settings
from support_agent.llm.embeddings import get_embeddings
from support_agent.memory.postgres_conn import get_postgres_pool, require_database_url
from support_agent.memory.sqlite_conn import open_sqlite_connection

_SUPPORTED_BACKENDS = ("memory", "sqlite", "postgres")


@dataclass
class AgentContext:
    """Runtime context passed at invoke time: identifies WHO we are talking to.

    This is how the agent's memory tools know under which `user_id` to read and
    write, so long-term memory stays scoped per customer.
    """

    user_id: str


def memories_namespace(user_id: str) -> tuple[str, str]:
    """The per-user long-term memory namespace. Isolation (R3) happens here.

    Defined ONCE because three places need the exact same tuple: the memory tools, the
    inspection and erasure surface, and the tests. A second hand-written copy would not
    raise anything if it drifted — it would read an empty namespace, so "forget my order
    number" would report success while deleting nothing.
    """
    return ("memories", user_id)


def _ttl_config(settings: Settings) -> TTLConfig | None:
    """Translate the retention setting into LangGraph's TTL config.

    LangGraph counts TTLs in MINUTES; we configure days, because that is how a retention
    policy is written down, and convert here once.

    The clock restarts on LAST ACCESS, not on creation (`refresh_on_read` defaults to
    true), so this is an *inactivity* retention — which is what support wants, but not
    what "delete after N days" sounds like.
    """
    if settings.memory_ttl_days is None:
        return None
    return TTLConfig(
        default_ttl=settings.memory_ttl_days * 24 * 60,
        sweep_interval_minutes=settings.memory_ttl_sweep_interval_minutes,
        refresh_on_read=True,
    )


def get_store(settings: Settings | None = None) -> BaseStore:
    """Return the configured long-term memory store.

    Semantic search over memories is always kept, reusing the same agnostic embeddings
    as the FAQ, so recall works by meaning rather than exact keywords. The storage
    backend is a config choice (`PERSISTENCE_BACKEND`):

        "memory"   ->  InMemoryStore:  lost when the process exits
        "sqlite"   ->  SqliteStore:    durable on disk, survives a restart
        "postgres" ->  PostgresStore:  durable on a server, with a GDPR sweeper

    Args:
        settings: Optional settings override (handy for tests).
    """
    settings = settings or get_settings()
    backend = settings.persistence_backend.lower()

    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unknown PERSISTENCE_BACKEND={settings.persistence_backend!r}. "
            f"Expected one of: {', '.join(_SUPPORTED_BACKENDS)}."
        )
    if backend == "postgres":
        require_database_url(settings.database_url)

    embeddings = get_embeddings(settings)
    dims = len(embeddings.embed_query("probe"))
    index = {"embed": embeddings, "dims": dims, "fields": ["text"]}

    if backend == "memory":
        return InMemoryStore(index=index)

    if backend == "sqlite":
        from langgraph.store.sqlite import SqliteStore

        store = SqliteStore(open_sqlite_connection(settings.agent_memory_db_path), index=index)
        store.setup()
        return store

    from langgraph.store.postgres import PostgresStore

    store = PostgresStore(
        get_postgres_pool(
            require_database_url(settings.database_url),
            settings.database_schema,
        ),
        index=index,
        ttl=_ttl_config(settings),
    )
    store.setup()
    if settings.memory_ttl_days is not None:
        store.start_ttl_sweeper()
    return store
