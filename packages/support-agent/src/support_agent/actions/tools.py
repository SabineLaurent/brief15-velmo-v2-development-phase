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

import logging

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from support_agent.actions.backend import OrderStatus, SupportBackend, Ticket
from support_agent.guardrails import ToolGuard
from support_agent.memory import AgentContext

logger = logging.getLogger(__name__)

# Prefix of the ticket opened when the AGENT decides a human is needed. Kept
# distinct from the router's fast-path escalation so the two are tellable apart
# in the backlog: one is "the bot tried and could not", the other is "this never
# should have waited".
HANDOFF_SUBJECT_PREFIX = "Handoff (agent-requested): "


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


# Shape of an order id when the adapter does not declare its own. An adapter
# overrides it with an `order_id_example` attribute (see `SqlSupportBackend`).
# This is prompt-visible text, not cosmetics: shown the wrong shape, the model
# invents ids in that shape when the customer has not supplied one.
DEFAULT_ORDER_ID_EXAMPLE = "CMD-1001"


def build_action_tools(
    backend: SupportBackend, tool_guard: ToolGuard | None = None
) -> list[BaseTool]:
    """Build the business action tools bound to a given backend adapter.

    Args:
        backend: The `SupportBackend` implementation the tools act through.
        tool_guard: Optional Phase 12-C hardening for the write tools (field
            validation, PII masking before persistence, rate limiting). `None`
            disables it — same behavior as before guardrails existed.
    """
    # Duck-typed, NOT part of the `SupportBackend` Protocol: how order ids are
    # spelled is a presentation hint, and widening the port for it would force
    # every future adapter to supply one.
    order_id_example = getattr(backend, "order_id_example", DEFAULT_ORDER_ID_EXAMPLE)

    @tool
    def get_order_status(order_id: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Look up the current status of a customer order.

        Use this whenever the customer asks about a specific order: where it is,
        whether it shipped, its tracking number, or its delivery date. If the
        customer has not given an order id, ask them for it before calling this
        tool — never guess one.
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
                f"Double-check the order id with them (it looks like "
                f"'{order_id_example}')."
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

        # Phase 12-C hardening on this side-effecting, persisting tool.
        if tool_guard is not None:
            error = tool_guard.validate_field(
                subject, field_name="subject"
            ) or tool_guard.validate_field(body, field_name="body")
            if error is not None:
                return error
            # Anti-abuse: cap how many tickets one customer can open in a window.
            if not tool_guard.allow_action(user_id):
                return (
                    "Too many tickets were opened recently for this customer. "
                    "Please try again later, or ask for a human agent if urgent."
                )
            # Never persist raw PII (e.g. a full card number) in a ticket.
            subject = tool_guard.sanitize(subject)
            body = tool_guard.sanitize(body)

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

    @tool
    def request_human_handoff(
        reason: str, summary: str, runtime: ToolRuntime[AgentContext]
    ) -> Command:
        """Hand this conversation over to a human advisor, and stop replying.

        Call this ONLY once you have actually tried: searched the FAQ, looked the
        order up, checked past tickets. The point of this agent is to spare humans
        the requests they add no value to, so a customer simply asking for "a
        human" is NOT enough on its own — try to solve it first, and hand over if
        you cannot, or if they insist after you offered help.

        DO hand over when a human genuinely adds value: the FAQ has no answer and
        no action of yours can resolve it, the customer is in a formal dispute, a
        decision requires authority you do not have, or they are clearly upset.

        `reason`: a few words on WHY a human is needed (e.g. "no FAQ answer for
        customs fees", "customer disputes a refund").
        `summary`: what the customer wants AND what you already tried, so the
        advisor does not have to reconstruct the case from scratch.
        """
        # user_id comes from the trusted runtime context, never from the model.
        user_id = runtime.context.user_id

        if tool_guard is not None:
            for text, name in ((reason, "reason"), (summary, "summary")):
                error = tool_guard.validate_field(text, field_name=name)
                if error:
                    return Command(
                        update={
                            "messages": [
                                ToolMessage(error, tool_call_id=runtime.tool_call_id)
                            ]
                        }
                    )
            if not tool_guard.allow_action(user_id):
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                "Rate limit reached for this customer: no handoff "
                                "opened. Tell them to try again later.",
                                tool_call_id=runtime.tool_call_id,
                            )
                        ]
                    }
                )
            # Never persist raw PII in a case the advisor will read.
            reason = tool_guard.sanitize(reason)
            summary = tool_guard.sanitize(summary)

        ticket = backend.create_ticket(
            user_id=user_id, subject=f"{HANDOFF_SUBJECT_PREFIX}{reason}", body=summary
        )
        logger.info(
            "Handoff requested by the agent: user=%s ticket=%s reason=%s",
            user_id,
            ticket.ticket_id,
            reason,
        )
        # `handled_by_human` mutes the bot from the NEXT turn on (the entry edge
        # reads it). The current turn still finishes normally, so the model can
        # tell the customer what just happened — with the case number.
        return Command(
            update={
                "handled_by_human": True,
                "messages": [
                    ToolMessage(
                        f"Case {ticket.ticket_id} handed over to a human advisor. "
                        "Tell the customer, give them this case number, and say "
                        "they can keep writing here.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )

    # Append the id shape to the LLM-facing description rather than baking it into
    # the docstring: the docstring is shared by every adapter, the example is not.
    get_order_status.description += (
        f" Order ids on this backend look like '{order_id_example}'."
    )

    return [
        get_order_status,
        create_ticket,
        list_customer_tickets,
        request_human_handoff,
    ]
