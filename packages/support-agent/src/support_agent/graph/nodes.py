"""The graph nodes (Phase 6): each node is one explicit step of the agent.

We deliberately "open the hood" of the prebuilt `create_agent` here:

    router   -> classifies the user's intent into one branch
    answer   -> small talk / greetings: a plain LLM reply, no tools
    model    -> the SUPPORT branch: LLM bound with tools (FAQ + memory)
    tools    -> executes the tool calls (ToolNode), then loops back to `model`
    escalate -> files the case, mutes the bot, hands off to a human (Phase 7)

Each node is a small function `(state) -> state update`. Making them explicit is
the whole point: the routing and the ReAct loop become objects we can read, draw
and trace in LangSmith, instead of being hidden inside a prebuilt agent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from support_agent.actions.backend import SupportBackend
from support_agent.graph.state import Route, SupportState
from support_agent.guardrails import InputGuard, OutputGuard, ToolGuard
from support_agent.llm import FALLBACK_EXCEPTIONS
from support_agent.memory import AgentContext

logger = logging.getLogger(__name__)

# Last-resort reply shown to the customer when an LLM call fails for good (all
# retries AND provider fallbacks exhausted). It is the ONE hard-coded user-facing
# string: with the LLM down we cannot localize it, so we keep it short and in the
# demo's language (French FAQ). Swap it for a localized/config value in real prod.
GRACEFUL_ERROR_MESSAGE = (
    "Désolé, je rencontre un problème technique momentané et ne peux pas traiter "
    "votre demande à l'instant. Merci de réessayer dans quelques instants ; si le "
    "problème persiste, un conseiller humain prendra le relais."
)

# Safety net in the seam (`api.py`): shown if the graph ever comes back PAUSED on
# an `interrupt()`. No node interrupts today — `escalate` hands off asynchronously
# (see below) — but the seam's invariant "every path delivers exactly one non-empty
# chunk" must survive the day a human-approval gate reintroduces one.
ESCALATION_PENDING_MESSAGE = (
    "Je transmets votre demande à un conseiller humain. Merci de patienter un "
    "instant : il prend le relais dans cette conversation."
)

# Delivered by `escalate` itself, once the case is filed and a human is on it.
ESCALATION_HANDOFF_MESSAGE = (
    "J'ai transmis votre demande à un conseiller humain (dossier {ticket_id}). "
    "Il vous répondra dès que possible. Vous pouvez continuer à écrire ici : vos "
    "messages seront joints à ce dossier."
)

# Delivered on every later turn of a thread a human has taken over. No LLM call:
# the bot is muted, not thinking.
HUMAN_TAKEOVER_MESSAGE = (
    "Votre demande est entre les mains d'un conseiller humain. Votre message a "
    "bien été enregistré et lui sera transmis."
)

# Subject of the ticket opened by an escalation. Stable on purpose: the backend
# derives the ticket id from its content, so re-running the node with the same
# customer message cannot open a second ticket.
ESCALATION_TICKET_SUBJECT = "Handoff to a human advisor"


def _with_fallbacks(
    primary: Runnable, fallbacks: Sequence[Runnable]
) -> Runnable:
    """Attach provider fallbacks to a (possibly tool-bound) runnable.

    `RunnableWithFallbacks` does not expose `bind_tools` / `with_structured_output`,
    so fallbacks MUST be composed at the leaf — after binding — which is exactly
    what each node factory does before calling this. Returns `primary` unchanged
    when no fallback is configured.
    """
    if not fallbacks:
        return primary
    return primary.with_fallbacks(
        list(fallbacks), exceptions_to_handle=FALLBACK_EXCEPTIONS
    )


# --- Input guard (Phase 12-A: the first thing raw customer text hits) ------


def make_guard_input(guard: InputGuard) -> Callable[[SupportState], dict]:
    """Build the entry guard node: validate, screen for injection, mask PII.

    Runs BEFORE the router. On a clean message it either passes through, or
    rewrites the customer's last message IN PLACE (same id, so the `add_messages`
    reducer replaces it) with a PII-masked version — the raw PII then never
    reaches the LLM, the tools, or the store. On a refused message it removes the
    offending text from history (so it cannot poison later turns), emits a safe
    reply, and flags `input_blocked` so the entry edge short-circuits to END.
    """

    def guard_input(state: SupportState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            None,
        )
        if last_human is None:
            return {"input_blocked": False}

        decision = guard.check(str(last_human.content))

        if decision.blocked:
            logger.warning("Input guard blocked a message: reason=%s", decision.reason)
            messages: list = []
            # Drop the offending message so it does not reach the LLM later.
            if last_human.id is not None:
                messages.append(RemoveMessage(id=last_human.id))
            messages.append(AIMessage(content=decision.user_message or GRACEFUL_ERROR_MESSAGE))
            return {"input_blocked": True, "messages": messages}

        updates: dict = {"input_blocked": False}
        if decision.pii_entities:
            logger.info(
                "Input guard masked PII: %s", ", ".join(decision.pii_entities)
            )
        # Overwrite the message in place only if masking actually changed it.
        if (
            decision.sanitized_text != str(last_human.content)
            and last_human.id is not None
        ):
            updates["messages"] = [
                HumanMessage(content=decision.sanitized_text, id=last_human.id)
            ]
        return updates

    return guard_input


def entry_route(state: SupportState) -> str:
    """Entry conditional edge: the three ways a turn can start.

    Order matters. A refused input ends the turn before anything else — an
    attacker gets no acknowledgement. A case a human owns skips the bot entirely
    (no LLM call at all). Everything else goes to the router, as before.
    """
    if state.get("input_blocked"):
        return END
    if state.get("handled_by_human"):
        return "human_takeover"
    return "router"


# --- Router ----------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = (
    "You are the router of a customer-support agent. Read the LAST user message "
    "(in the context of the conversation) and classify it into exactly one route:\n"
    "- 'support': any request for information OR action about the store — orders, "
    "delivery, returns, refunds, payment, account, warranty, OR what the store "
    "sells and whether a product or service is available; anything that needs the "
    "FAQ or the memory of what the customer told you before; OR any action the "
    "agent can take on the customer's behalf, including looking up an order and "
    "opening a support ticket for a defect or follow-up (the agent can create "
    "tickets itself). When in doubt about a factual or product question, choose "
    "'support': it checks the FAQ instead of guessing.\n"
    "- 'escalate': ONLY when the customer must reach a human right now — they "
    "explicitly ask for a human agent, or the case needs a live human decision "
    "(legal, formal dispute, distress). This files the case for a human advisor "
    "and STOPS the bot from replying in this conversation, so choose it only when "
    "a human is really required. Opening a ticket the agent handles itself does "
    "NOT belong here: that is 'support'.\n"
    "- 'answer': ONLY purely social messages that carry no informational request — "
    "greetings, thanks, goodbyes, small talk. If the message asks for ANY fact or "
    "action, it is 'support', not 'answer'.\n"
    "Answer with the route only."
)


class RouteDecision(BaseModel):
    """Structured output for the router: which branch to take next."""

    route: Route = Field(description="The single branch to route this message to.")


def make_router(
    model: BaseChatModel, fallbacks: Sequence[BaseChatModel] = ()
) -> Callable[[SupportState], dict]:
    """Build the router node: an LLM classification that writes `route` to state."""
    # Structured output => the LLM must return a valid `RouteDecision`, so we get
    # a clean enum value instead of parsing free text. Fallbacks are composed at
    # the leaf (each model gets the SAME structured-output binding, then we chain).
    classifier = _with_fallbacks(
        model.with_structured_output(RouteDecision),
        [m.with_structured_output(RouteDecision) for m in fallbacks],
    )

    def router(state: SupportState) -> dict:
        messages = [SystemMessage(ROUTER_SYSTEM_PROMPT), *state["messages"]]
        try:
            decision: RouteDecision = classifier.invoke(messages)
            return {"route": decision.route}
        except Exception:
            # Classification is unavailable (LLM down, or unparsable output). Fail
            # safe to the lightest branch: `answer` will emit a graceful reply if
            # the LLM is truly down, rather than crashing the whole turn.
            logger.exception("Router classification failed; defaulting to 'answer'.")
            return {"route": "answer"}

    return router


def route_from_state(state: SupportState) -> Route:
    """The conditional edge: read the decision the router stored in state."""
    return state["route"]


# --- Answer (small talk) ---------------------------------------------------

ANSWER_SYSTEM_PROMPT = (
    "You are a friendly customer-support agent for an online store. This message "
    "is small talk or a greeting: reply briefly and warmly in the user's language. "
    "Do not invent facts about orders or policies."
)


def make_answer(
    model: BaseChatModel, fallbacks: Sequence[BaseChatModel] = ()
) -> Callable[[SupportState], dict]:
    """Build the small-talk node: a plain LLM reply, no tools."""
    chain = _with_fallbacks(model, list(fallbacks))

    def answer(state: SupportState) -> dict:
        messages = [SystemMessage(ANSWER_SYSTEM_PROMPT), *state["messages"]]
        try:
            reply = chain.invoke(messages)
            return {"messages": [reply]}
        except Exception:
            logger.exception("Answer node LLM call failed; returning graceful reply.")
            return {"messages": [AIMessage(content=GRACEFUL_ERROR_MESSAGE)]}

    return answer


# --- Support (the explicit ReAct loop) -------------------------------------

SUPPORT_SYSTEM_PROMPT = (
    "You are a helpful customer-support agent for an online store. "
    "For any factual question (orders, delivery, returns, refunds, payment, "
    "account, warranty...), ALWAYS call the `search_faq` tool first and answer "
    "ONLY from the retrieved content — never guess. Cite the source file you "
    "used (e.g. 'source : livraison.md'). If the FAQ does not contain the "
    "answer, say so honestly and suggest contacting a human agent. "
    "You also have a long-term memory about the current customer: call "
    "`search_memories` when the user refers to something they told you before "
    "(their name, preferences, past orders), and call `save_memory` when they "
    "share a durable fact worth remembering across sessions. "
    "You can also take actions on the customer's behalf: call `get_order_status` "
    "to look up a specific order (ask for the order id if missing), and "
    "`create_ticket` to open a human follow-up when the FAQ cannot resolve the "
    "request but it does not need to pause the conversation. Only take an action "
    "when the customer actually asks for it, and confirm the result (order status "
    "or ticket number) back to them. "
    "When the customer reports a problem that might be recurring (e.g. another "
    "delivery issue), call `list_customer_tickets` first to check their history: "
    "if a similar past ticket exists, acknowledge that it happened before instead "
    "of treating it as new. "
    "Answer concisely, in the user's language, and use both the conversation "
    "history and your memories to stay consistent."
)


def make_support_model(
    model: BaseChatModel,
    tools: list[BaseTool],
    fallbacks: Sequence[BaseChatModel] = (),
) -> Callable[[SupportState], dict]:
    """Build the support node: the LLM step of the ReAct loop (LLM + tools).

    This node decides whether to answer or to call a tool. The `tools` node runs
    the calls, then loops back here — that back-and-forth IS the ReAct loop we
    were getting for free from `create_agent`, now made explicit.

    Runs entirely on the STRONG model. We measured a per-pass cascade (fast model
    for the tool-decision pass) and it did NOT help TTFT on our Azure deployment —
    the small model was even slower on the tool-bound decision call, because the
    cost is the round-trip + long prompt, not the model size. Only the `router`
    keeps the fast model. See `docs/latence.md`.
    """
    # Each model (primary + fallbacks) gets the SAME tools bound, then we chain
    # them: if the primary provider is down, the fallback answers with tools too.
    model_with_tools = _with_fallbacks(
        model.bind_tools(tools),
        [m.bind_tools(tools) for m in fallbacks],
    )

    def support_model(state: SupportState) -> dict:
        messages = [SystemMessage(SUPPORT_SYSTEM_PROMPT), *state["messages"]]
        try:
            reply = model_with_tools.invoke(messages)
            return {"messages": [reply]}
        except Exception:
            logger.exception("Support node LLM call failed; returning graceful reply.")
            return {"messages": [AIMessage(content=GRACEFUL_ERROR_MESSAGE)]}

    return support_model


# --- Escalate (human-in-the-loop handoff) ----------------------------------


def make_escalate(
    backend: SupportBackend, tool_guard: ToolGuard | None = None
) -> Callable[[SupportState, Runtime[AgentContext]], dict]:
    """Build the handoff node: file the case, mute the bot, END THE TURN.

    This node used to call `interrupt()` and leave the graph paused until someone
    resumed it. That was wrong for two reasons, one fatal:

    - Fatal: LangGraph resumes the PENDING TASK before anything else, so every
      later message on the thread re-entered this node and interrupted again. The
      router was never re-evaluated: the conversation was dead, silently, and no
      log said so. Nothing could unblock it because no operator console exists.
    - Conceptual: `interrupt()` means "I must not proceed without a human
      decision". In a handoff the agent is not about to DO anything — it is
      passing the case on. There is nothing to hold back.

    What production support platforms do instead: the conversation is a row with a
    status and an assignee, and escalating REASSIGNS it (bot -> human queue). The
    customer is never blocked; the bot is muted; a human is notified. That is what
    this node now does, with the ticket as the durable, listable case object.

    `interrupt()` is not deleted from the project, it is relocated: its right home
    is an approval gate before an IRREVERSIBLE action (refund, cancellation).

    Idempotent: the ticket id is derived from its content, so re-executing the
    node with the same customer message cannot open a second ticket.
    """

    def escalate(state: SupportState, runtime: Runtime[AgentContext]) -> dict:
        user_id = runtime.context.user_id
        last_user_message = next(
            (
                m.content
                for m in reversed(state["messages"])
                if isinstance(m, HumanMessage)
            ),
            "",
        )
        body = str(last_user_message)
        if tool_guard is not None:
            # Same hygiene as the ticket TOOL: never persist raw PII in a case,
            # even though `guard_input` already masked the message in place.
            body = tool_guard.sanitize(body)

        ticket = backend.create_ticket(
            user_id=user_id, subject=ESCALATION_TICKET_SUBJECT, body=body
        )
        # Traceable by design: an escalation is a business event, not a silent
        # branch. This is the line that was missing when the thread died.
        logger.info(
            "Escalated to a human: user=%s ticket=%s", user_id, ticket.ticket_id
        )
        return {
            "messages": [
                AIMessage(
                    content=ESCALATION_HANDOFF_MESSAGE.format(
                        ticket_id=ticket.ticket_id
                    )
                )
            ],
            "handled_by_human": True,
        }

    return escalate


def human_takeover(state: SupportState) -> dict:
    """Acknowledge a message on a thread a human already owns. No LLM call.

    The bot is muted, not thinking: answering here would mean talking over the
    advisor on their own case. The message itself is kept — it is already in
    `messages`, hence in the checkpointer, hence readable by whoever picks the
    case up.

    Note this flag is also what makes escalation self-limiting: once set, the
    router is never reached again on this thread, so no second ticket can be
    opened by a customer repeating "I want a human".
    """
    return {"messages": [AIMessage(content=HUMAN_TAKEOVER_MESSAGE)]}


# --- Output guard (Phase 12-B: the last check before the reply leaves) ------


def make_guard_output(guard: OutputGuard) -> Callable[[SupportState], dict]:
    """Build the exit guard node: screen the reply for leaks, redact PII/secrets.

    Runs on every path that answers the customer (answer / support / escalate
    resume). It overwrites the last AI message IN PLACE (same id) with the
    sanitized version, or with a safe message if the reply leaked our system
    prompt. A clean reply passes through untouched (no state update).
    """

    def guard_output(state: SupportState) -> dict:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or last.id is None:
            return {}

        decision = guard.check(str(last.content))
        if not decision.replaced and decision.sanitized_text == str(last.content):
            return {}  # nothing to change

        if decision.replaced:
            logger.warning("Output guard replaced a reply leaking the system prompt.")
        elif decision.findings:
            logger.info(
                "Output guard redacted from reply: %s", ", ".join(decision.findings)
            )
        return {"messages": [AIMessage(content=decision.sanitized_text, id=last.id)]}

    return guard_output
