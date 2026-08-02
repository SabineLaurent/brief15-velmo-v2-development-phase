"""CLI: populate (or rebuild) the SQL business double.

    python -m support_agent.actions.sql.seed             # idempotent, safe
    python -m support_agent.actions.sql.seed --reset     # drop, recreate, reseed
    python -m support_agent.actions.sql.seed --db-path /tmp/shop.db

This one ACTS by default, unlike `make consolidate` and `make memory`: those touch
customer memory, whereas this writes fixture data into a double we are free to throw
away. The guarantee here is a different one — a plain run never destroys anything (it
refuses if the shop is already populated), and destruction is opt-in via `--reset`.

`--reset` exists because `create_all()` creates MISSING tables and does not alter
existing ones: add a column to a model and every shop file already on disk keeps the old
shape, with no error, until a query fails far from the cause. For a double with a
deterministic seed, dropping and rebuilding is cheaper and more reliable than migrating
— which is why this project has no Alembic setup.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from support_agent.actions.sql.sampledata import seed
from support_agent.actions.sql.schema import (
    Base,
    Customer,
    make_engine,
    session_factory_for_path,
)
from support_agent.config import get_settings

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m support_agent.actions.sql.seed",
        description="Populate the SQL business double with the reference dataset.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the shop SQLite file (default: SHOP_DB_PATH from .env).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="DROP every table first, then recreate and reseed. Destroys tickets "
        "opened during past conversations.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    path = args.db_path or get_settings().shop_db_path

    if args.reset:
        engine = make_engine(f"sqlite:///{path}")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        logger.info("Schema dropped and recreated: %s", path)

    sessions = session_factory_for_path(path)
    with sessions() as session:
        if session.scalars(select(Customer).limit(1)).first() is not None:
            logger.info(
                "Shop already populated: %s — nothing to do "
                "(use --reset to rebuild it from scratch).",
                path,
            )
            return 0
        seed(session)

    logger.info("Business double seeded: %s", path)
    logger.info("Set SUPPORT_BACKEND=sqlite in .env to have the agent use it.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
