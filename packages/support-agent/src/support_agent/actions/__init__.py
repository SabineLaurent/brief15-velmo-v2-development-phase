"""Business actions layer (Phase 8): tools that DO something.

    backend -> the `SupportBackend` port + a demo in-memory adapter
    tools   -> get_order_status / create_ticket, acting through the backend

The point of the port is agnosticism to the business project: swap the adapter
to plug the agent into a real order service or ticketing system, without
touching the tools or the graph.
"""

from support_agent.actions.backend import (
    InMemorySupportBackend,
    OrderStatus,
    SupportBackend,
    Ticket,
    get_backend,
)
from support_agent.actions.tools import build_action_tools

__all__ = [
    "SupportBackend",
    "InMemorySupportBackend",
    "OrderStatus",
    "Ticket",
    "get_backend",
    "build_action_tools",
]
