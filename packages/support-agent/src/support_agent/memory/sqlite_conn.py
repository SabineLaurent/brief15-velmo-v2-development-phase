"""Shared SQLite connection helper for the durable memory backends.

Both the checkpointer (short term) and the store (long term) can persist to the
same on-disk SQLite database. Each opens its own connection to that file via
this helper, so the connection lifecycle is ours to manage (we keep it open for
the process lifetime) instead of relying on a context manager.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_sqlite_connection(path: str) -> sqlite3.Connection:
    """Open (creating parent dirs) a SQLite connection usable across threads.

    Two settings matter:

    - `check_same_thread=False`: LangGraph may touch the saver / store from a
      different thread than the one that opened the connection (concurrent
      evaluation, a web server request pool).
    - `isolation_level=None` (autocommit): the LangGraph SQLite backends issue
      their OWN explicit `BEGIN` transactions. Python's `sqlite3` default opens
      an implicit transaction before DML, which then collides with that explicit
      `BEGIN` ("cannot start a transaction within a transaction"). Autocommit mode
      hands transaction control to the backend, which is exactly what it expects.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path, check_same_thread=False, isolation_level=None)
