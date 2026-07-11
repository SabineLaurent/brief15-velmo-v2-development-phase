"""Mémoire long terme factuelle : backend Postgres (prod) et repli SQLite hors-ligne.

Miroir de `kb_store.py` / `episodic.py` : backend réel choisi par variable
d'environnement (`DB_URL`), repli hors-ligne sinon. Différence assumée avec
l'épisodique : là où `LocalEpisodicStore` vit dans un magasin RAM neuf à
chaque instanciation de `MemoryManager`, le repli ici doit survivre à la
création d'un nouveau `MemoryManager` — c'est le sens même de la
persistance multi-session (R2). Le repli utilise donc un engine SQLite
**mis en cache au niveau du module** (une seule fois par process), et non
un par instance — sur le modèle de `db.fresh_sqlite_session()`, en gardant
la connexion vivante au lieu d'en ouvrir une nouvelle à chaque appel.
"""

from __future__ import annotations

import os
from typing import Protocol

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from ..db import Base, MemoryFact


class FactsStore(Protocol):
    """Interface minimale d'un backend de mémoire long terme factuelle."""

    def set(self, user_id: str, key: str, value: str) -> None: ...
    def all_for(self, user_id: str) -> dict[str, str]: ...
    def forget(self, user_id: str, target: str) -> int: ...


class SqlFactsStore:
    """Faits durables persistés via SQLAlchemy (Postgres en prod, SQLite hors-ligne)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def set(self, user_id: str, key: str, value: str) -> None:
        with self._session_factory() as session:
            existing = session.get(MemoryFact, (user_id, key))
            if existing is not None:
                existing.value = value
            else:
                session.add(MemoryFact(user_id=user_id, key=key, value=value))
            session.commit()

    def all_for(self, user_id: str) -> dict[str, str]:
        with self._session_factory() as session:
            rows = session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id)).all()
            return {row.key: row.value for row in rows}

    def forget(self, user_id: str, target: str) -> int:
        target = target.lower()
        with self._session_factory() as session:
            rows = session.scalars(select(MemoryFact).where(MemoryFact.user_id == user_id)).all()
            matches = [row for row in rows if target in row.key.lower() or target in row.value.lower()]
            for row in matches:
                session.delete(row)
            session.commit()
            return len(matches)


_local_session_factory: sessionmaker[Session] | None = None


def _get_local_session_factory() -> sessionmaker[Session]:
    """Engine SQLite en mémoire, créé une seule fois puis réutilisé (persistance intra-process)."""
    global _local_session_factory
    if _local_session_factory is None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        _local_session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _local_session_factory


def get_facts_store() -> FactsStore:
    """Renvoie le backend Postgres si `DB_URL` est configuré, sinon le repli SQLite."""
    if not os.getenv("DB_URL"):
        return SqlFactsStore(_get_local_session_factory())
    from ..db import session_factory

    return SqlFactsStore(session_factory())
