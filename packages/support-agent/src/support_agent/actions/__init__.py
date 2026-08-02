"""Business actions layer: tools that DO something.

    backend -> the `SupportBackend` port + the in-memory demo adapter
    sql/    -> the SQL business double (schema + seed + `SqlSupportBackend`)
    tools   -> get_order_status / create_ticket, acting through the backend

The point of the port is agnosticism to the business project: swap the adapter to plug
the agent into a real order service or ticketing system, without touching the tools or
the graph. `SUPPORT_BACKEND` picks which adapter `get_backend()` returns — the two
shipped here prove the port is real rather than decorative.
"""

from support_agent.actions.backend import (
    InMemorySupportBackend,
    OrderStatus,
    SupportBackend,
    Ticket,
    get_backend,
    reset_backend_cache,
)
from support_agent.actions.tools import build_action_tools

__all__ = [
    "SupportBackend",
    "InMemorySupportBackend",
    "OrderStatus",
    "Ticket",
    "get_backend",
    "reset_backend_cache",
    "build_action_tools",
]
