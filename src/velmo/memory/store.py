"""Memory persistence, isolated per user.

Two complementary relational levels:

-`memory_facts`: durable key-value facts (source of structured truth);
-`memory_episodes`: chronological log of exchanges (recall basis).

Portable SQLAlchemy backend: Postgres if `MEMORY_DB_URL` is defined, otherwise one
shared SQLite file (`temp_db/memory.db` at the repo root) which ensures true persistence
multi-session offline. The semantic episodic level (Chroma) can be
plug in later behind the same `MemoryStore` interface.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, create_engine, delete, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

from ..config import memory_backend, warn_backend_unavailable

Turn = tuple[str, str]  # (role, content)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryFact(Base):
    """Durable fact on a user (e.g. shoe size=L). Unique key per user."""

    __tablename__ = "memory_facts"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MemoryEpisode(Base):
    """A turn of conversation retained (role + content), timestamped for the trace."""

    __tablename__ = "memory_episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


def _default_url() -> str:
    if memory_backend() == "postgres":
        env = os.getenv("MEMORY_DB_URL") or os.getenv("DB_URL")
        if env:
            return env
        warn_backend_unavailable("mémoire Postgres", "MEMORY_DB_URL/DB_URL absent")
    path = Path(__file__).resolve().parents[3] / "temp_db" / "memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


# An engine (therefore a sessionmaker) per URL, shared between process instances.
_SESSIONMAKERS: dict[str, sessionmaker] = {}


def _sessionmaker_for(url: str) -> sessionmaker:
    sm = _SESSIONMAKERS.get(url)
    if sm is None:
        engine = create_engine(url, future=True)
        Base.metadata.create_all(engine)
        sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        _SESSIONMAKERS[url] = sm
    return sm


class MemoryStore:
    """Persistent access to facts and episodes, filtered by `user_id`."""

    def __init__(self, url: str | None = None) -> None:
        self._sm = _sessionmaker_for(url or _default_url())

    # --- structured facts ------------------------------------------------

    def upsert_fact(self, user_id: str, key: str, value: str) -> None:
        with self._sm() as s:
            row = s.get(MemoryFact, (user_id, key))
            if row is None:
                s.add(MemoryFact(user_id=user_id, key=key, value=value))
            else:
                row.value = value
                row.updated_at = _now()
            s.commit()

    def facts(self, user_id: str) -> dict[str, str]:
        with self._sm() as s:
            rows = s.execute(
                select(MemoryFact).where(MemoryFact.user_id == user_id)
            ).scalars()
            return {r.key: r.value for r in rows}

    # --- episodic diary --------------------------------------------------

    def add_episode(self, user_id: str, role: str, content: str) -> None:
        with self._sm() as s:
            s.add(MemoryEpisode(user_id=user_id, role=role, content=content))
            s.commit()

    def episodes(self, user_id: str) -> list[Turn]:
        "User's episodes, in chronological order."
        with self._sm() as s:
            rows = s.execute(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.id)
            ).scalars()
            return [(r.role, r.content) for r in rows]

    # --- right to be forgotten ------------------------------------------------------

    def forget(self, user_id: str, target: str) -> int:
        """Deletes user facts and episodes mentioning `target`.

        Case-insensitive matching on the key/value of a fact or the
        content of an episode. Returns the total number of rows deleted.
        """
        needle = target.strip().lower()
        if not needle:
            return 0
        removed = 0
        with self._sm() as s:
            facts = s.execute(
                select(MemoryFact).where(MemoryFact.user_id == user_id)
            ).scalars().all()
            for f in facts:
                if needle in f.key.lower() or needle in f.value.lower():
                    s.delete(f)
                    removed += 1
            episodes = s.execute(
                select(MemoryEpisode).where(MemoryEpisode.user_id == user_id)
            ).scalars().all()
            for e in episodes:
                if needle in e.content.lower():
                    s.delete(e)
                    removed += 1
            s.commit()
        return removed

    def purge(self, user_id: str) -> None:
        """Efface toute la mémoire d'un utilisateur (utile pour les tests)."""
        with self._sm() as s:
            s.execute(delete(MemoryFact).where(MemoryFact.user_id == user_id))
            s.execute(delete(MemoryEpisode).where(MemoryEpisode.user_id == user_id))
            s.commit()
