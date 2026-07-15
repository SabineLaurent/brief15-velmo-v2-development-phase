"""Business action tools (Phase 8): tools that DO something, not just read.

Until now every tool was read-only (search the FAQ, recall a memory). These are
**action** tools: `get_order_status` looks a customer's order up in the business
backend, and `create_ticket` opens a real support ticket (a side effect).

Two design rules that come with actions:

- They go through the `SupportBackend` port (see `backend.py`), never a concrete
  system — that is what keeps the agent agnostic to the business project.
- The customer identity (`user_id`) for `create_ticket` comes from the runtime
  context, NOT from the LLM, so the model cannot open a ticket in someone else's
  name. Same isolation principle as the long-term memory tools.
"""

from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from support_agent.actions.backend import OrderStatus, SupportBackend, Ticket
from support_agent.memory import AgentContext


def _format_order(order: OrderStatus) -> str:
    """Render an order as a compact, readable block for the LLM to relay."""
    lines = [f"Order {order.order_id}: {order.status}"]
    if order.carrier:
        lines.append(f"Carrier: {order.carrier}")
    if order.tracking_number:
        lines.append(f"Tracking number: {order.tracking_number}")
    if order.estimated_delivery:
        lines.append(f"Estimated delivery: {order.estimated_delivery}")
    return "\n".join(lines)


def _format_tickets(tickets: list[Ticket]) -> str:
    """Render a customer's ticket history as a compact list for the LLM."""
    lines = [
        f"- {t.ticket_id} [{t.status}] {t.subject}" for t in tickets
    ]
    return "\n".join(lines)


def build_action_tools(backend: SupportBackend) -> list[BaseTool]:
    """Build the business action tools bound to a given backend adapter.

    Args:
        backend: The `SupportBackend` implementation the tools act through.
    """

    @tool
    def get_order_status(order_id: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Look up the current status of a customer order.

        Use this whenever the customer asks about a specific order: where it is,
        whether it shipped, its tracking number, or its delivery date. The
        `order_id` looks like 'CMD-1001'. If the customer has not given an order
        id, ask them for it before calling this tool.
        """
        # user_id comes from the trusted runtime context: the backend only
        # returns the order if it belongs to THIS customer (never from the LLM).
        user_id = runtime.context.user_id
        order = backend.get_order_status(order_id, user_id)
        if order is None:
            # Same message whether the order is unknown or owned by someone else
            # (do not reveal that a foreign order id exists).
            return (
                f"No order '{order_id}' found on this customer's account. "
                "Double-check the order id with them (it looks like 'CMD-1001')."
            )
        return _format_order(order)

    @tool
    def create_ticket(
        subject: str, body: str, runtime: ToolRuntime[AgentContext]
    ) -> str:
        """Open a support ticket for the current customer.

        Use this when the request cannot be resolved from the FAQ and needs a
        human follow-up (a defect, a refund dispute, a specific account change),
        but does NOT require pausing the conversation. Write a short `subject`
        and put the customer's request and any useful context in `body`. Confirm
        the ticket number back to the customer.
        """
        # user_id comes from the trusted runtime context, never from the model.
        user_id = runtime.context.user_id
        ticket = backend.create_ticket(user_id=user_id, subject=subject, body=body)
        return (
            f"Ticket {ticket.ticket_id} created for this customer "
            f"(subject: {ticket.subject}). Status: {ticket.status}."
        )

    @tool
    def list_customer_tickets(runtime: ToolRuntime[AgentContext]) -> str:
        """List the current customer's past support tickets (their history).

        Use this to check whether an issue has happened before (recurrence): when
        the customer reports a problem that may be recurring, or refers to a past
        request, look up their tickets first so you can acknowledge the history
        instead of treating it as brand new. Takes no argument — the customer is
        identified from the trusted runtime context.
        """
        # user_id comes from the trusted runtime context, never from the model.
        user_id = runtime.context.user_id
        tickets = backend.list_tickets(user_id)
        if not tickets:
            return "This customer has no previous support tickets."
        return "Previous tickets for this customer:\n" + _format_tickets(tickets)

    return [get_order_status, create_ticket, list_customer_tickets]
