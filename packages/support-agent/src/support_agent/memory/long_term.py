"""Long-term (cross-session) memory: the Store factory + runtime context.

Where the *checkpointer* (short-term) remembers ONE conversation, keyed by
`thread_id`, the *Store* (long-term) remembers a USER across conversations,
keyed by `user_id`. Different question, different tool:

    checkpointer  ->  "what did we say earlier in THIS chat?"   (thread_id)
    store         ->  "what do I know about THIS customer?"     (user_id)

The store organizes data by hierarchical *namespaces* (tuples), so one
customer's memories are physically separated from another's.

The backend is a config choice (`PERSISTENCE_BACKEND`), same agnostic idea as
the LLM and checkpointer factories — and in all three cases the semantic search
over memories is preserved, only the engine underneath changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.store.base import BaseStore, TTLConfig
from langgraph.store.memory import InMemoryStore

from support_agent.config import Settings, get_settings
from support_agent.llm.embeddings import get_embeddings
from support_agent.memory.postgres_conn import get_postgres_pool, require_database_url
from support_agent.memory.sqlite_conn import open_sqlite_connection

# The backend names this factory knows how to build, in the same order as the
# docstring below. `get_checkpointer` accepts exactly these three: `PERSISTENCE_BACKEND`
# is ONE variable driving BOTH factories, so the two lists must never drift apart.
_SUPPORTED_BACKENDS = ("memory", "sqlite", "postgres")


@dataclass
class AgentContext:
    """Runtime context passed at invoke time: identifies WHO we are talking to.

    This is how the agent's memory tools know under which `user_id` to read and
    write, so long-term memory stays scoped per customer.
    """

    user_id: str


def _ttl_config(settings: Settings) -> TTLConfig | None:
    """Translate the retention setting into LangGraph's TTL config.

    Two details that bite if taken for granted:

    - **LangGraph counts TTLs in MINUTES**, not seconds. We configure retention
      in days because that is how a retention policy is actually written down,
      and convert here, once.
    - The clock restarts on **last access**, not on creation (`refresh_on_read`
      defaults to true). So this is an *inactivity* retention: a customer we
      never hear from again is forgotten after the delay; an active one keeps
      their memories. That is the behaviour we want for support — but it is not
      what "delete after N days" sounds like, hence this note.
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

    The store always keeps *semantic search* over memories — we reuse the same
    agnostic embeddings as the FAQ (Phase 4), so recall works by meaning, not
    exact keywords. The storage backend is a config choice (`PERSISTENCE_BACKEND`),
    exactly like the checkpointer:

        "memory"   ->  InMemoryStore:  lost when the process exits
        "sqlite"   ->  SqliteStore:    durable on disk, survives a restart
        "postgres" ->  PostgresStore:  durable on a server, with a GDPR sweeper

    Args:
        settings: Optional settings override (handy for tests).
    """
    settings = settings or get_settings()
    backend = settings.persistence_backend.lower()

    # CONFIG FIRST, I/O SECOND. Both checks below run BEFORE the embeddings probe,
    # and the order is the feature: otherwise a configuration bug (a typo in the
    # backend name, a missing DATABASE_URL) surfaces as an HTTP error coming from
    # the embeddings provider, and the check meant to report it can only be reached
    # by a machine that already holds valid credentials. A config bug must never
    # cost a network round trip — nor a billed embeddings call — to be named.
    #
    # `get_checkpointer` already rejects an unknown name before touching anything;
    # validating here keeps the two factories symmetrical, which matters because a
    # SINGLE variable (`PERSISTENCE_BACKEND`) drives both. One accepting what the
    # other refuses would mean a half-configured process.
    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unknown PERSISTENCE_BACKEND={settings.persistence_backend!r}. "
            f"Expected one of: {', '.join(_SUPPORTED_BACKENDS)}."
        )
    if backend == "postgres":
        # Refuse a missing connection string HERE, before the probe below spends a
        # network call. The branch at the bottom validates again — it is a pure,
        # idempotent check, and paying it twice is cheaper than a call site that no
        # longer says which value it trusts.
        require_database_url(settings.database_url)

    embeddings = get_embeddings(settings)
    # Probe once to learn the vector size instead of hard-coding a per-model
    # dimension — keeps the store provider-agnostic like everything else.
    dims = len(embeddings.embed_query("probe"))
    index = {"embed": embeddings, "dims": dims, "fields": ["text"]}

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

    # Only "postgres" can reach this point — the name was validated above, so there
    # is no trailing `raise` to fall through to. Keeping a final unreachable branch
    # would be dead code pretending to be a safety net.
    from langgraph.store.postgres import PostgresStore

    # Same `index` config as the other two backends — the semantic search is
    # identical, only the engine underneath changes (pgvector instead of
    # sqlite-vec instead of numpy in RAM). Pool shared with the checkpointer:
    # working memory and agent memory are one database, per §3 of
    # docs/architecture-cible-2026-07-25.md.
    store = PostgresStore(
        get_postgres_pool(
            require_database_url(settings.database_url),
            settings.database_schema,
        ),
        index=index,
        ttl=_ttl_config(settings),
    )
    store.setup()  # creates the store tables + the pgvector index
    if settings.memory_ttl_days is not None:
        # Without this, the TTL is only metadata: rows carry an expiry date
        # that nothing ever acts on. The sweeper is the thread that makes
        # "the agent forgets" actually happen.
        store.start_ttl_sweeper()
    return store
