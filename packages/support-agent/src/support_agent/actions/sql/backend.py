"""A `SupportBackend` adapter backed by SQL (the business double, on disk).

The second adapter behind the `actions/` port, and the point of the port: neither the
tools nor the graph know which one they are talking to, and swapping them is one `.env`
variable (`SUPPORT_BACKEND`), exactly like swapping an LLM provider.

Over the in-memory adapter it buys durability and a realistic dataset (14 orders across
10 customers, shipments, returns, refunds) instead of three hand-written rows.

It is a DOUBLE, not a system of record: in production we unplug it and call the
merchant's API. Hence its home under `database/shop/` (gitignored runtime state) and the
freedom to drop and reseed it at will.

The port maps onto two tables, not one: an order's carrier and tracking number live in
`shipments`, so `get_order_status` outer-joins the two.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from support_agent.actions.backend import OrderStatus, Ticket, _ticket_id
from support_agent.actions.sql.schema import Customer
from support_agent.actions.sql.schema import Order as OrderRow
from support_agent.actions.sql.schema import Shipment as ShipmentRow
from support_agent.actions.sql.schema import Ticket as TicketRow
from support_agent.actions.sql.schema import session_factory_for_path

logger = logging.getLogger(__name__)


class SqlSupportBackend:
    """A `SupportBackend` reading and writing the SQL business double."""

    order_id_example = "O-2024-0103"

    def __init__(self, sessions: sessionmaker) -> None:
        self._sessions = sessions

    @classmethod
    def from_path(cls, path: str) -> SqlSupportBackend:
        """Open the double at `path`, refusing to run against an unseeded file.

        The refusal is the important part. `create_all()` happily creates empty
        tables, and an empty shop answers "no order found" for *every* lookup —
        a silent, plausible-looking failure that reads like a broken agent rather
        than a missing seed. Failing at startup with the fix in the message costs
        one line and saves that debugging session.
        """
        sessions = session_factory_for_path(path)
        with sessions() as session:
            if session.scalars(select(Customer).limit(1)).first() is None:
                raise RuntimeError(
                    f"The business double at '{path}' has no data. "
                    "Run `make seed` to populate it, or set SUPPORT_BACKEND=memory "
                    "to use the in-memory demo adapter instead."
                )
        return cls(sessions)

    # --- The port ----------------------------------------------------------

    def get_order_status(self, order_id: str, user_id: str) -> OrderStatus | None:
        """Return the order IF it belongs to `user_id`, else None.

        Authorization is enforced in the WHERE clause rather than after the fetch:
        a foreign order never leaves the database, so there is nothing to leak by
        forgetting a later check.
        """
        normalised = order_id.strip().upper()
        with self._sessions() as session:
            row = session.execute(
                select(OrderRow, ShipmentRow)
                .outerjoin(ShipmentRow, ShipmentRow.order_id == OrderRow.id)
                .where(OrderRow.id == normalised, OrderRow.customer_id == user_id)
            ).first()
        if row is None:
            return None
        order, shipment = row
        return OrderStatus(
            order_id=order.id,
            owner_id=order.customer_id,
            status=order.status.value,
            carrier=shipment.carrier if shipment else None,
            tracking_number=shipment.tracking_number if shipment else None,
            estimated_delivery=(
                (shipment.actual_delivery or shipment.estimated_delivery) if shipment else None
            ),
        )

    def create_ticket(self, user_id: str, subject: str, body: str) -> Ticket:
        """Open a ticket, idempotently.

        Idempotency comes from the schema, not from a check-then-insert: the
        primary key IS the hash of (user_id, subject, body), so a retry with the
        same content collides on the key. We look the row up first and return it,
        which makes the common case one SELECT and no exception handling.
        """
        ticket_id = _ticket_id(user_id, subject, body)
        with self._sessions() as session:
            existing = session.get(TicketRow, ticket_id)
            if existing is not None:
                return _to_ticket(existing)
            self._ensure_customer(session, user_id)
            row = TicketRow(
                id=ticket_id, customer_id=user_id, subject=subject, body=body, status="open"
            )
            session.add(row)
            session.commit()
            return _to_ticket(row)

    def list_tickets(self, user_id: str) -> list[Ticket]:
        """Return this customer's tickets, oldest first (most recent last)."""
        with self._sessions() as session:
            rows = session.scalars(
                select(TicketRow)
                .where(TicketRow.customer_id == user_id)
                .order_by(TicketRow.created_at, TicketRow.id)
            ).all()
        return [_to_ticket(row) for row in rows]

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _ensure_customer(session: Session, user_id: str) -> None:
        """Create a placeholder customer row if `user_id` is unknown.

        `tickets.customer_id` is a foreign key, so an unknown customer would fail the
        insert on Postgres and silently dangle on SQLite — the worse of the two.

        The leniency is a property of the DOUBLE, deliberately not of the port: a real
        ticketing system owns customer creation and would reject an unknown id. Here
        `user_id` comes from the runtime context and may be any string. Logged at INFO,
        because a placeholder appearing in production would mean the double was still
        plugged in.
        """
        if session.get(Customer, user_id) is not None:
            return
        logger.info("Business double: creating a placeholder customer for '%s'", user_id)
        session.add(
            Customer(
                id=user_id,
                email=f"{user_id}@placeholder.invalid",
                full_name=user_id,
            )
        )
        session.flush()


def _to_ticket(row: TicketRow) -> Ticket:
    """Map a SQL row onto the port's provider-neutral `Ticket`."""
    return Ticket(
        ticket_id=row.id,
        user_id=row.customer_id,
        subject=row.subject,
        body=row.body,
        status=row.status,
    )
