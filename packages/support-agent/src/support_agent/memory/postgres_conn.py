"""Shared Postgres connection pool for the durable memory backends.

A pool, not a single connection: the HTTP server answers requests from a thread pool and
the store's TTL sweeper runs on its own thread, so one connection would serialize all of
them behind a lock.

One pool for BOTH memory horizons. SQLite uses two files so each is readable and
deletable on its own; Postgres keeps working memory and agent memory in the same
database, separated by schema.

Two connection options are mandatory, not stylistic: `autocommit=True` (otherwise
`setup()` never commits its `CREATE TABLE`) and `row_factory=dict_row` (the LangGraph
backends read rows by name).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 30.0


@lru_cache
def get_postgres_pool(url: str, schema: str) -> ConnectionPool:
    """Return the process-wide connection pool, opening it on first use.

    Cached on its arguments so the checkpointer and the store share one pool. The schema
    and the pgvector extension are created if missing, so a blank database becomes a
    working one without a manual migration step.

    Args:
        url: Postgres connection string (`postgres://user:pass@host:5432/db`).
        schema: Schema our tables live in; created if absent.
    """
    pool = ConnectionPool(
        url,
        kwargs={"autocommit": True, "row_factory": dict_row},
        configure=lambda conn: _configure_connection(conn, schema),
        min_size=1,
        max_size=10,
        open=False,
    )
    _prepare_database(pool, url, schema)
    return pool


def _prepare_database(pool: ConnectionPool, url: str, schema: str) -> None:
    """Open the pool, waiting for the server, then ensure schema + extension."""
    pool.open(wait=True, timeout=_CONNECT_TIMEOUT_S)

    with pool.connection() as conn:
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
