"""Persistance de la mémoire, isolée par utilisateur.

Deux étages relationnels complémentaires :

- `memory_facts` : faits durables clé-valeur (source de vérité structurée) ;
- `memory_episodes` : journal chronologique des échanges (base du rappel).

Backend SQLAlchemy portable : Postgres si `MEMORY_DB_URL` est défini, sinon un
unique fichier SQLite (`temp_db/memory.db` à la racine du dépôt) qui garantit une
vraie persistance multi-session hors-ligne. L'étage épisodique sémantique
(Chroma) pourra se brancher plus tard derrière la même interface `MemoryStore`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from ..config import memory_backend, warn_backend_unavailable

class Turn(NamedTuple):
    """Un tour de conversation : rôle (« user »/« assistant ») et contenu.

    `NamedTuple` : reste un tuple (dépaquetage et indexation inchangés) tout en
    exposant `.role` / `.content` là où seul un champ est lu.
    """

    role: str
    content: str


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryFact(Base):
    """Fait durable sur un utilisateur (ex. taille=L). Clé unique par utilisateur."""

    __tablename__ = "memory_facts"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MemoryEpisode(Base):
    """Un tour de conversation retenu (rôle + contenu), horodaté pour la traçabilité."""

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


# Un engine (donc un sessionmaker) par URL, partagé entre instances du process.
_SESSIONMAKERS: dict[str, sessionmaker[Session]] = {}


def _sessionmaker_for(url: str) -> sessionmaker[Session]:
    sm = _SESSIONMAKERS.get(url)
    if sm is None:
        engine = create_engine(url, future=True)
        Base.metadata.create_all(engine)
        sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        _SESSIONMAKERS[url] = sm
    return sm


class MemoryStore:
    """Accès persistant aux faits et épisodes, filtrés par `user_id`."""

    def __init__(self, url: str | None = None) -> None:
        self._sm = _sessionmaker_for(url or _default_url())

    # --- faits structurés ------------------------------------------------

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

    # --- journal épisodique ----------------------------------------------

    def add_episode(self, user_id: str, role: str, content: str) -> None:
        with self._sm() as s:
            s.add(MemoryEpisode(user_id=user_id, role=role, content=content))
            s.commit()

    def episodes(self, user_id: str) -> list[Turn]:
        "Épisodes de l'utilisateur, par ordre chronologique."
        with self._sm() as s:
            rows = s.execute(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.id)
            ).scalars()
            return [Turn(r.role, r.content) for r in rows]

    # --- droit à l'oubli -------------------------------------------------

    def forget(self, user_id: str, target: str) -> int:
        """Supprime les faits et épisodes d'un utilisateur mentionnant `target`.

        Correspondance insensible à la casse sur la clé/valeur d'un fait ou le
        contenu d'un épisode. Renvoie le nombre total de lignes supprimées.
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
