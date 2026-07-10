"""Peuple la base Postgres avec le jeu de données de référence Velmo.

Usage : uv run python scripts/seed.py
"""

from __future__ import annotations

from dotenv import load_dotenv

from velmo.db import Base, make_engine, session_factory
from velmo.sampledata import seed


def main() -> None:
    load_dotenv()
    engine = make_engine()
    Base.metadata.create_all(engine)
    session = session_factory()()
    # Idempotence simple : ne pas re-seeder si des clients existent déjà.
    from velmo.db import Customer
    from velmo.tools._common import select

    if session.scalars(select(Customer)).first() is not None:
        print("Base déjà peuplée — rien à faire.")
        return
    seed(session)
    print("Base Velmo peuplée (catalogue, clients, commandes).")


if __name__ == "__main__":
    main()
