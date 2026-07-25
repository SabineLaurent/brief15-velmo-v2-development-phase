"""Short-term (conversation) memory: the checkpointer factory.

A *checkpointer* persists the agent's state after every step, keyed by
`thread_id`. Replaying the same `thread_id` continues the same conversation —
that is what "the agent remembers the discussion" concretely means.

The backend is a config choice (`PERSISTENCE_BACKEND`), same agnostic idea as
the LLM factory:

    "memory"  ->  InMemorySaver: fast, zero-setup, but lost when the process exits
    "sqlite"  ->  SqliteSaver:   durable on disk, survives a restart

    "postgres" ->  PostgresSaver: durable on a server, the deployment target

Switching backend is a `.env` change, not a code change.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from support_agent.config import Settings, get_settings
from support_agent.memory.postgres_conn import get_postgres_pool, require_database_url
from support_agent.memory.sqlite_conn import open_sqlite_connection


def get_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """Return the configured short-term memory checkpointer.

    Args:
        settings: Optional settings override (handy for tests). Defaults to the
            process-wide cached settings.
    """
    settings = settings or get_settings()
    backend = settings.persistence_backend.lower()

    if backend == "memory":
        return InMemorySaver()

    if backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        # We own the connection (kept open for the process lifetime), so we build
        # the saver directly instead of using the `from_conn_string` context
        # manager. `setup()` creates the checkpoint tables on first use.
        saver = SqliteSaver(open_sqlite_connection(settings.working_memory_db_path))
        saver.setup()
        return saver

    if backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        # Same shape as the SQLite branch, and that is the whole point: the
        # graph never learns which one it got. `from_conn_string` is a context
        # manager (it closes the connection on exit), so — exactly as in SQLite —
        # we own the connection instead, here a pool shared with the store.
        pool = get_postgres_pool(
            require_database_url(settings.database_url),
            settings.database_schema,
        )
        saver = PostgresSaver(pool)
        saver.setup()  # creates the checkpoint tables on first use
        return saver

    raise ValueError(
        f"Unknown PERSISTENCE_BACKEND={settings.persistence_backend!r}. "
        f"Expected one of: memory, sqlite, postgres."
    )
