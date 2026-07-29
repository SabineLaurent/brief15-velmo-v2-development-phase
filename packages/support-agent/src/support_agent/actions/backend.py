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

import hashlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# --- Domain objects: the typed results the backend returns -----------------


@dataclass(frozen=True)
class OrderStatus:
    """The status of a customer order, as returned by the backend."""

    order_id: str
    owner_id: str  # the user_id this order belongs to (for authorization)
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

    def get_order_status(self, order_id: str, user_id: str) -> OrderStatus | None:
        """Return the order IF it belongs to `user_id`, else None.

        Authorization lives here, in the backend: the tool passes the trusted
        `user_id` and the adapter refuses to reveal another customer's order.
        Returning None for both "unknown" and "not yours" avoids leaking whether
        an order id exists (no enumeration).
        """
        ...

    def create_ticket(self, user_id: str, subject: str, body: str) -> Ticket:
        """Open a support ticket for a user and return the created ticket.

        Must be idempotent: calling it twice with the same (user_id, subject,
        body) returns the same ticket instead of opening a duplicate.
        """
        ...

    def list_tickets(self, user_id: str) -> list[Ticket]:
        """Return this user's support tickets (their history), most recent last.

        Scoped to `user_id` like every other method: a customer only ever sees
        their own tickets. This is the authoritative way to tell whether an issue
        has already happened before (recurrence), rather than relying on memory.
        """
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
        if not self.tickets:
            self.tickets = _seed_tickets()

    def get_order_status(self, order_id: str, user_id: str) -> OrderStatus | None:
        # Normalise so "cmd-1001", "CMD-1001" and stray spaces all match.
        order = self.orders.get(order_id.strip().upper())
        # Ownership check: never reveal an order that belongs to someone else.
        if order is None or order.owner_id != user_id:
            return None
        return order

    def create_ticket(self, user_id: str, subject: str, body: str) -> Ticket:
        # Deterministic id from the request content => idempotent: a retry with
        # the same (user_id, subject, body) yields the same id, so we return the
        # existing ticket instead of opening a duplicate.
        ticket_id = _ticket_id(user_id, subject, body)
        for existing in self.tickets:
            if existing.ticket_id == ticket_id:
                return existing
        ticket = Ticket(
            ticket_id=ticket_id, user_id=user_id, subject=subject, body=body
        )
        self.tickets.append(ticket)
        return ticket

    def list_tickets(self, user_id: str) -> list[Ticket]:
        # Ownership scoping, like every other method: only this user's tickets.
        return [t for t in self.tickets if t.user_id == user_id]


def _ticket_id(user_id: str, subject: str, body: str) -> str:
    """A deterministic ticket id derived from the request content (idempotency).

    The NUL separators keep the fields unambiguous so that distinct requests
    cannot collide by concatenation (e.g. subject "ab"+body "c" vs "a"+"bc").
    """
    digest = hashlib.sha1(f"{user_id}\x00{subject}\x00{body}".encode()).hexdigest()
    return f"TICKET-{digest[:8].upper()}"


def _seed_orders() -> dict[str, OrderStatus]:
    """A few example orders so `get_order_status` has data to return.

    CMD-1003 belongs to another customer on purpose: it lets the demo show the
    ownership check refusing to reveal someone else's order.
    """
    orders = [
        OrderStatus(
            order_id="CMD-1001",
            owner_id="demo-user",
            status="shipped",
            carrier="Colissimo",
            tracking_number="6A123456789FR",
            estimated_delivery="2026-07-18",
        ),
        OrderStatus(
            order_id="CMD-1002",
            owner_id="demo-user",
            status="processing",
            estimated_delivery="2026-07-22",
        ),
        OrderStatus(order_id="CMD-1003", owner_id="other-user", status="delivered"),
    ]
    return {order.order_id: order for order in orders}


def _seed_tickets() -> list[Ticket]:
    """A past ticket so `list_tickets` can demonstrate recurrence detection.

    demo-user already had a delivery problem (resolved) on an earlier order:
    the agent can now discover, deterministically, that this is not the first
    time — instead of guessing from memory.
    """
    return [
        _seed_ticket(
            user_id="demo-user",
            subject="Colis marqué livré mais non reçu (CMD-0990)",
            body="Le suivi indiquait 'livré' mais le client n'a rien reçu.",
            status="resolved",
        ),
    ]


def _seed_ticket(user_id: str, subject: str, body: str, status: str) -> Ticket:
    """Build a seeded ticket with the same content-derived id as create_ticket."""
    return Ticket(
        ticket_id=_ticket_id(user_id, subject, body),
        user_id=user_id,
        subject=subject,
        body=body,
        status=status,
    )


# One shared backend per process, like `get_store()` for memory, and built FROM
# CONFIG rather than hardcoded — that is what makes the port real: two adapters
# exist, and `.env` picks one without the tools or the graph changing.
_BACKEND: SupportBackend | None = None


def get_backend() -> SupportBackend:
    """Return the process-wide business backend, chosen by `SUPPORT_BACKEND`.

    Imports the SQL adapter lazily: it pulls in SQLAlchemy and touches the
    filesystem, and the default `memory` path should pay for neither.
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    # Imported here to avoid a circular import at module scope: `config` is
    # cheap, but the SQL adapter imports THIS module for `_ticket_id`.
    from support_agent.config import get_settings

    choice = get_settings().support_backend.strip().lower()
    if choice == "memory":
        _BACKEND = InMemorySupportBackend()
    elif choice == "sqlite":
        from support_agent.actions.sql.backend import SqlSupportBackend

        _BACKEND = SqlSupportBackend.from_path(get_settings().shop_db_path)
    else:
        # Unknown value = configuration error, not a silent fallback: falling back
        # to the demo adapter would look like it worked and quietly serve three
        # fake orders in production.
        raise ValueError(
            f"Unknown SUPPORT_BACKEND '{choice}'. Expected 'memory' or 'sqlite'."
        )
    return _BACKEND


def reset_backend_cache() -> None:
    """Drop the cached backend (tests that switch `SUPPORT_BACKEND` need this)."""
    global _BACKEND
    _BACKEND = None
