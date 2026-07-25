"""Shared Postgres connection pool for the durable memory backends.

The Postgres counterpart of `sqlite_conn.py`, with three differences that are
worth knowing rather than discovering in production:

1. **A pool, not a single connection.** The HTTP server answers requests from a
   thread pool (`api.py` bridges sync -> async with `asyncio.to_thread`), and the
   store's TTL sweeper runs on its own thread too. A single connection would
   serialize all of them behind one lock. LangGraph accepts either, so we hand it
   a pool and let concurrent turns actually be concurrent.

2. **One pool for BOTH memory horizons.** SQLite uses two files so each is
   readable and deletable on its own; Postgres keeps working memory and agent
   memory in the same database, separated by schema. Sharing the pool is what
   makes that one database instead of two clients pretending.

3. **Two connection options are mandatory, not stylistic.** `autocommit=True`
   (otherwise `setup()` never commits its `CREATE TABLE`) and
   `row_factory=dict_row` (the LangGraph backends read rows by name, so tuple
   rows fail with `TypeError: tuple indices must be integers`). Both are called
   out explicitly in the langgraph-checkpoint-postgres README.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# How long to wait for the database on startup. A container stack boots its
# services in parallel, so the agent routinely wins the race against Postgres.
# Waiting here turns the classic first-deployment crash into a few seconds of
# patience. Compose's `depends_on: service_healthy` already covers the common
# case; this is the belt to that pair of braces (and it is what protects a
# managed database that briefly refuses connections during failover).
_CONNECT_TIMEOUT_S = 30.0


@lru_cache
def get_postgres_pool(url: str, schema: str) -> ConnectionPool:
    """Return the process-wide connection pool, opening it on first use.

    Cached on its arguments so the checkpointer and the store share one pool.
    The schema (and the pgvector extension) are created if missing, so a blank
    database becomes a working one without a manual migration step.

    Args:
        url: Postgres connection string (`postgres://user:pass@host:5432/db`).
        schema: Schema our tables live in; created if absent.
    """
    pool = ConnectionPool(
        url,
        # Applied to every connection the pool creates, including replacements
        # for ones dropped by a restart or an idle timeout.
        kwargs={"autocommit": True, "row_factory": dict_row},
        configure=lambda conn: _configure_connection(conn, schema),
        min_size=1,
        max_size=10,
        # Opening in the constructor is deprecated in psycopg_pool; we open
        # explicitly below so the wait (and its failure) is ours to control.
        open=False,
    )
    _prepare_database(pool, url, schema)
    return pool


def _prepare_database(pool: ConnectionPool, url: str, schema: str) -> None:
    """Open the pool, waiting for the server, then ensure schema + extension."""
    pool.open(wait=True, timeout=_CONNECT_TIMEOUT_S)

    with pool.connection() as conn:
        # `configure` already created the schema for this connection; what is
        # left is the vector extension, which the long-term store needs for
        # semantic search over memories.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    logger.info("Postgres memory backend ready (schema=%s)", schema)


def _configure_connection(conn, schema: str) -> None:  # type: ignore[no-untyped-def]
    """Point a fresh connection at our schema, creating it if needed.

    `search_path` is per-session, so it must be set on every connection rather
    than once at startup — that is exactly what the pool's `configure` hook is
    for. `public` stays on the path so the `vector` type remains resolvable
    wherever the extension ended up being installed.
    """
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))


def require_database_url(url: str | None) -> str:
    """Return the configured `DATABASE_URL`, or fail with an actionable message.

    Called at boot by both memory factories. Failing here — loudly, before the
    graph is built — is the point: a missing connection string discovered inside
    a customer's request is the same bug, found at the worst possible moment.
    """
    if not url:
        raise ValueError(
            "PERSISTENCE_BACKEND=postgres requires DATABASE_URL to be set "
            "(e.g. postgres://agent:agent@postgres:5432/agent). "
            "See .env.example section 6."
        )
    return url
