"""The SQL business double: schema, reference dataset, and the port adapter.

    schema     -> SQLAlchemy models (customers, catalogue, orders, tickets...)
    sampledata -> the reference dataset `seed(session)` inserts
    backend    -> `SqlSupportBackend`, the `SupportBackend` adapter over them
    seed       -> the `make seed` CLI

A double, not a system of record: in production the merchant owns this data and
we call their API instead. See `database/README.md`.
"""

from support_agent.actions.sql.backend import SqlSupportBackend
from support_agent.actions.sql.sampledata import DEMO_CUSTOMER_ID, seed

__all__ = ["SqlSupportBackend", "seed", "DEMO_CUSTOMER_ID"]
