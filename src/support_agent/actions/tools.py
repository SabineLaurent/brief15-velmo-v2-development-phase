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

from support_agent.actions.backend import OrderStatus, SupportBackend
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


def build_action_tools(backend: SupportBackend) -> list[BaseTool]:
    """Build the business action tools bound to a given backend adapter.

    Args:
        backend: The `SupportBackend` implementation the tools act through.
    """

    @tool
    def get_order_status(order_id: str) -> str:
        """Look up the current status of a customer order.

        Use this whenever the customer asks about a specific order: where it is,
        whether it shipped, its tracking number, or its delivery date. The
        `order_id` looks like 'CMD-1001'. If the customer has not given an order
        id, ask them for it before calling this tool.
        """
        order = backend.get_order_status(order_id)
        if order is None:
            return (
                f"No order found with id '{order_id}'. Double-check the order id "
                "with the customer (it looks like 'CMD-1001')."
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

    return [get_order_status, create_ticket]
