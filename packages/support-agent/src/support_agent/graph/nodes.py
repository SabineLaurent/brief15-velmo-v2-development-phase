"""The graph nodes: each node is one explicit step of the agent.

    router   -> classifies the user's intent into one branch
    answer   -> small talk / greetings: a plain LLM reply, no tools
    model    -> the SUPPORT branch: LLM bound with tools (FAQ + memory)
    tools    -> executes the tool calls (ToolNode), then loops back to `model`
    escalate -> files the case, mutes the bot, hands off to a human

Each node is a small function `(state) -> state update`, which keeps the routing and the
ReAct loop readable, drawable and traceable in LangSmith.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from support_agent.actions.backend import SupportBackend
from support_agent.graph.state import Route, SupportState
from support_agent.guardrails import InputGuard, OutputGuard, ToolGuard
from support_agent.llm import FALLBACK_EXCEPTIONS
from support_agent.memory import AgentContext
from support_agent.memory.compaction import format_summary
from support_agent.memory.episodic import (
    format_episodes,
    recall_episodes,
    record_candidate,
)

logger = logging.getLogger(__name__)

GRACEFUL_ERROR_MESSAGE = (
    "Désolé, je rencontre un problème technique momentané et ne peux pas traiter "
    "votre demande à l'instant. Merci de réessayer dans quelques instants ; si le "
    "problème persiste, un conseiller humain prendra le relais."
)

ESCALATION_PENDING_MESSAGE = (
    "Je transmets votre demande à un conseiller humain. Merci de patienter un "
    "instant : il prend le relais dans cette conversation."
)

ESCALATION_HANDOFF_MESSAGE = (
    "J'ai transmis votre demande à un conseiller humain (dossier {ticket_id}). "
    "Il vous répondra dès que possible. Vous pouvez continuer à écrire ici : vos "
    "messages seront joints à ce dossier."
)

HUMAN_TAKEOVER_MESSAGE = (
    "Votre demande est entre les mains d'un conseiller humain. Votre message a "
    "bien été enregistré et lui sera transmis. Si vous avez une autre question "
    "en attendant, répondez « reprendre » et je me remets à votre disposition."
)

TAKEOVER_RELEASED_MESSAGE = (
    "C'est noté, je reprends la main. Votre dossier reste ouvert auprès du "
    "conseiller. Que puis-je faire pour vous ?"
)

_TAKEOVER_RELEASE_PATTERN = re.compile(
    r"\b(reprendre|reprends|autre question|nouvelle demande|laisse tomber)\b",
    re.IGNORECASE,
)

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


# --- Input guard (the first thing raw customer text hits) ------------------


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

        decision = guard.check(last_human.text)

        if decision.blocked:
            logger.warning("Input guard blocked a message: reason=%s", decision.reason)
            messages: list = []
            if last_human.id is not None:
                messages.append(RemoveMessage(id=last_human.id))
            messages.append(AIMessage(content=decision.user_message or GRACEFUL_ERROR_MESSAGE))
            return {"input_blocked": True, "messages": messages}

        updates: dict = {"input_blocked": False}
        if decision.pii_entities:
            logger.info(
                "Input guard masked PII: %s", ", ".join(decision.pii_entities)
            )
        if decision.sanitized_text != last_human.text and last_human.id is not None:
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
    "- 'escalate': ONLY when a human must take over IMMEDIATELY, with no attempt "
    "worth making first — a formal legal dispute, a threat, distress, or an "
    "explicitly urgent human decision. This files the case and STOPS the bot from "
    "replying in this conversation.\n"
    "  A customer merely ASKING for a human is NOT this route: choose 'support' "
    "so the agent can try to solve it (it can hand over itself, once it has "
    "tried). The whole point of this agent is to spare humans the requests they "
    "add no value to, so escalating before trying is a failure, not a courtesy.\n"
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
        system_prompt = ANSWER_SYSTEM_PROMPT + format_summary(state.get("summary") or "")
        messages = [SystemMessage(system_prompt), *state["messages"]]
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
    "answer, say so honestly. "
    "You also have a long-term memory about the current customer: call "
    "`search_memories` when the user refers to something they told you before "
    "(their name, preferences, past orders), and call `save_memory` when they "
    "share a durable fact worth remembering across sessions. "
    "When the customer asks you to FORGET something about them ('forget my order "
    "number', 'delete what you know about my address'), call `forget_memory` with "
    "their own words. It is their right and it is irreversible: never call it "
    "unasked, and report exactly what was deleted — if it reports that nothing "
    "matched, say you hold no such information instead of confirming a deletion "
    "that did not happen. "
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
    "When the customer asks to speak to a human, do NOT hand over on the spot: "
    "this agent exists to spare humans the requests they add no value to. Ask "
    "what they need and try to solve it (FAQ, order lookup, ticket). Hand over "
    "with `request_human_handoff` once you have genuinely tried and cannot "
    "resolve it, if they insist after you offered help, or if the case needs a "
    "decision you cannot make. Pass a `summary` that says what they want AND what "
    "you already tried, so the advisor does not start from zero. "
    "Answer concisely, in the user's language, and use both the conversation "
    "history and your memories to stay consistent."
)


def make_support_model(
    model: BaseChatModel,
    tools: list[BaseTool],
    fallbacks: Sequence[BaseChatModel] = (),
    episodic: EpisodicRecall | None = None,
) -> Callable[[SupportState], dict]:
    """Build the support node: the LLM step of the ReAct loop (LLM + tools).

    Decides whether to answer or to call a tool; the `tools` node runs the calls, then
    loops back here.

    Runs entirely on the STRONG model. A per-pass cascade (fast model for the tool-
    decision pass) was measured and did not help TTFT: the cost is the round-trip plus
    the long prompt, not the model size. Only the router keeps the fast model.

    Episodic memory is injected HERE rather than offered as a tool, unlike
    `search_memories`. A model that is floundering does not know it should ask for a
    worked example, which is precisely when one is worth most — so the lookup is
    unconditional on this branch, and it costs one embedding call rather than an LLM
    round trip. `None` disables it.
    """
    model_with_tools = _with_fallbacks(
        model.bind_tools(tools),
        [m.bind_tools(tools) for m in fallbacks],
    )

    def support_model(state: SupportState) -> dict:
        system_prompt = SUPPORT_SYSTEM_PROMPT
        system_prompt += format_summary(state.get("summary") or "")
        if episodic is not None:
            system_prompt += episodic.block_for(state["messages"])
        messages = [SystemMessage(system_prompt), *state["messages"]]
        try:
            reply = model_with_tools.invoke(messages)
            return {"messages": [reply]}
        except Exception:
            logger.exception("Support node LLM call failed; returning graceful reply.")
            return {"messages": [AIMessage(content=GRACEFUL_ERROR_MESSAGE)]}

    return support_model


class EpisodicRecall:
    """Reads past cases out of the store and renders them for the prompt.

    A small object rather than a bare function so the node stays readable and the
    store is bound ONCE at build time — the support node has no business knowing
    where episodes live, only that it can ask for a prompt block.
    """

    def __init__(self, store: BaseStore, limit: int, min_score: float = 0.0) -> None:
        self._store = store
        self._limit = limit
        self._min_score = min_score
        self._memo_key: str | None = None
        self._memo_block: str = ""

    def block_for(self, messages: Sequence[object]) -> str:
        """The few-shot block for the current situation, or empty if there is none.

        The query is the customer's LAST message, not the whole thread: embedding the
        full history would drown "what is being asked right now" in small talk.

        Computed once per customer message, not once per model call. This node is the
        LLM step of a ReAct LOOP, so it runs again after every tool result on input that
        has not changed. Keyed by message ID rather than text, so the memo self-
        invalidates on the next turn without a TTL.
        """
        last_human = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
        )
        if last_human is None:
            return ""

        key = last_human.id
        if key is not None and self._memo_key == key:
            return self._memo_block

        episodes = recall_episodes(
            self._store,
            query=last_human.text,
            limit=self._limit,
            min_score=self._min_score,
        )
        if episodes:
            logger.info("Episodic recall: injecting %d past case(s).", len(episodes))
        block = format_episodes(episodes)
        if key is not None:
            self._memo_key, self._memo_block = key, block
        return block


# --- Escalate (human-in-the-loop handoff) ----------------------------------


def make_escalate(
    backend: SupportBackend, tool_guard: ToolGuard | None = None
) -> Callable[[SupportState, Runtime[AgentContext]], dict]:
    """Build the handoff node: file the case, mute the bot, END THE TURN.

    It deliberately does not call `interrupt()`. LangGraph resumes the pending task
    before anything else, so every later message re-entered this node and interrupted
    again: the conversation was dead, silently, with no operator console to unblock it.
    And `interrupt()` means "I must not proceed without a human decision", whereas a
    handoff has nothing to hold back — its right home is an approval gate before an
    irreversible action.

    Idempotent: the ticket id is derived from its content, so re-executing the node on
    the same customer message cannot open a second ticket.
    """

    def escalate(state: SupportState, runtime: Runtime[AgentContext]) -> dict:
        user_id = runtime.context.user_id
        body = next(
            (
                m.text
                for m in reversed(state["messages"])
                if isinstance(m, HumanMessage)
            ),
            "",
        )
        if tool_guard is not None:
            body = tool_guard.sanitize(body)

        ticket = backend.create_ticket(
            user_id=user_id, subject=ESCALATION_TICKET_SUBJECT, body=body
        )
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

    Answering here would mean talking over the advisor on their own case. The message is
    kept in `messages`, hence in the checkpointer, for whoever picks the case up.

    The flag also makes a handoff self-limiting: the router is never reached again on
    this thread, so a customer repeating "I want a human" cannot stack cases. A
    deterministic way back exists, because a handoff is a judgement and judgements are
    sometimes wrong; releasing does not close the case.
    """
    last_user_message = next(
        (m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    if _TAKEOVER_RELEASE_PATTERN.search(last_user_message):
        logger.info("Customer released the human takeover on this thread.")
        return {
            "handled_by_human": False,
            "messages": [AIMessage(content=TAKEOVER_RELEASED_MESSAGE)],
        }
    return {"messages": [AIMessage(content=HUMAN_TAKEOVER_MESSAGE)]}


# --- Output guard (the last check before the reply leaves) -----------------


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

        decision = guard.check(last.text)
        if not decision.replaced and decision.sanitized_text == last.text:
            return {}

        if decision.replaced:
            logger.warning("Output guard replaced a reply leaking the system prompt.")
        elif decision.findings:
            logger.info(
                "Output guard redacted from reply: %s", ", ".join(decision.findings)
            )
        return {"messages": [AIMessage(content=decision.sanitized_text, id=last.id)]}

    return guard_output


# --- Close turn (the write side of episodic memory) ------------------------


def make_close_turn(store: BaseStore) -> Callable[[SupportState, RunnableConfig], dict]:
    """Build the last node of every answering path: flag the thread for learning.

    The cheap half of episodic memory: one upsert, no embedding, no model. Distilling
    costs an LLM call, so it runs offline (`memory/consolidate.py`) — doing it here
    would add a sequential hop to the turn and would distil a fragment whose outcome is
    not known yet.

    Two filters decide what the agent is allowed to learn: only the `support` branch
    produces candidates, and `resolved` is False as soon as a human owns the thread, so
    the few-shot pool is not poisoned by its own failures. The takeover path is recorded
    rather than skipped, which is what flips an existing candidate to unresolved.
    """

    def close_turn(state: SupportState, config: RunnableConfig) -> dict:
        handled_by_human = bool(state.get("handled_by_human"))
        if not handled_by_human and state.get("route") != "support":
            return {}

        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return {}

        record_candidate(
            store, thread_id=str(thread_id), resolved=not handled_by_human
        )
        return {}

    return close_turn
