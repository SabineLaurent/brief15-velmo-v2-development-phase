"""The business backend port (Phase 8): where actions actually happen.

Same philosophy as the LLM factory: the agent must stay **agnostic to the
business project**, not only to the LLM provider. So the action tools never talk
to a concrete order database or ticketing system directly. They talk to a
`SupportBackend` — a small interface (a "port") that a real project implements
with an adapter (REST API, SQL, an internal SDK...).

For the tutorial we ship one adapter: `InMemorySupportBackend`, a fake backend
with seeded orders and an in-memory ticket list. Plugging in a real system means
writing another adapter that satisfies the same `Protocol` — no change to the
tools or the graph, exactly like swapping an LLM provider via `.env`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# --- Domain objects: the typed results the backend returns -----------------


@dataclass(frozen=True)
class OrderStatus:
    """The status of a customer order, as returned by the backend."""

    order_id: str
    status: str  # e.g. "shipped", "processing", "delivered", "cancelled"
    carrier: str | None = None
    tracking_number: str | None = None
    estimated_delivery: str | None = None  # ISO date, kept as a string for the demo


@dataclass(frozen=True)
class Ticket:
    """A support ticket created for a customer."""

    ticket_id: str
    user_id: str
    subject: str
    body: str
    status: str = "open"


# --- The port: what any business backend must provide ----------------------


@runtime_checkable
class SupportBackend(Protocol):
    """The interface every business adapter implements.

    Keep this deliberately small and provider-neutral: it is the contract the
    action tools depend on. A real project implements it against its own systems
    (order service, Zendesk/Jira, ...) without the tools ever knowing.
    """

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """Return the status of an order, or None if it does not exist."""
        ...

    def create_ticket(self, user_id: str, subject: str, body: str) -> Ticket:
        """Open a support ticket for a user and return the created ticket."""
        ...


# --- The demo adapter: an in-memory fake backend ---------------------------


@dataclass
class InMemorySupportBackend:
    """A fake `SupportBackend` for the tutorial: no external system required.

    Orders are seeded so the demo has something to look up. Tickets accumulate in
    a plain list. A real adapter would replace the bodies of these methods with
    calls to actual services — the signatures stay identical.
    """

    orders: dict[str, OrderStatus] = field(default_factory=dict)
    tickets: list[Ticket] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.orders:
            self.orders = _seed_orders()

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        # Normalise so "cmd-1001", "CMD-1001" and stray spaces all match.
        return self.orders.get(order_id.strip().upper())

    def create_ticket(self, user_id: str, subject: str, body: str) -> Ticket:
        ticket = Ticket(
            ticket_id=f"TICKET-{uuid.uuid4().hex[:8].upper()}",
            user_id=user_id,
            subject=subject,
            body=body,
        )
        self.tickets.append(ticket)
        return ticket


def _seed_orders() -> dict[str, OrderStatus]:
    """A few example orders so `get_order_status` has data to return."""
    orders = [
        OrderStatus(
            order_id="CMD-1001",
            status="shipped",
            carrier="Colissimo",
            tracking_number="6A123456789FR",
            estimated_delivery="2026-07-18",
        ),
        OrderStatus(
            order_id="CMD-1002",
            status="processing",
            estimated_delivery="2026-07-22",
        ),
        OrderStatus(order_id="CMD-1003", status="delivered"),
    ]
    return {order.order_id: order for order in orders}


# One shared demo backend per process, like `get_store()` for memory. A real app
# would build the adapter from config (`.env`) here instead of hardcoding it.
_BACKEND: SupportBackend | None = None


def get_backend() -> SupportBackend:
    """Return the process-wide business backend (the demo in-memory adapter)."""
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = InMemorySupportBackend()
    return _BACKEND
