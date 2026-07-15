"""The graph nodes (Phase 6): each node is one explicit step of the agent.

We deliberately "open the hood" of the prebuilt `create_agent` here:

    router   -> classifies the user's intent into one branch
    answer   -> small talk / greetings: a plain LLM reply, no tools
    model    -> the SUPPORT branch: LLM bound with tools (FAQ + memory)
    tools    -> executes the tool calls (ToolNode), then loops back to `model`
    escalate -> hands off to a human (placeholder; Phase 7 = real interrupt)

Each node is a small function `(state) -> state update`. Making them explicit is
the whole point: the routing and the ReAct loop become objects we can read, draw
and trace in LangSmith, instead of being hidden inside a prebuilt agent.
"""

from __future__ import annotations

from typing import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from support_agent.graph.state import Route, SupportState
from support_agent.memory import AgentContext

# --- Router ----------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = (
    "You are the router of a customer-support agent. Read the LAST user message "
    "(in the context of the conversation) and classify it into exactly one route:\n"
    "- 'support': any factual request about orders, delivery, returns, refunds, "
    "payment, account or warranty, OR anything that needs the FAQ or the memory "
    "of what the customer told you before.\n"
    "- 'escalate': the user explicitly asks for a human, is filing a complaint, "
    "or the request clearly needs a human decision (legal, dispute, distress).\n"
    "- 'answer': greetings, thanks, small talk, or a simple message that needs "
    "no lookup.\n"
    "Answer with the route only."
)


class RouteDecision(BaseModel):
    """Structured output for the router: which branch to take next."""

    route: Route = Field(description="The single branch to route this message to.")


def make_router(model: BaseChatModel) -> Callable[[SupportState], dict]:
    """Build the router node: an LLM classification that writes `route` to state."""
    # Structured output => the LLM must return a valid `RouteDecision`, so we get
    # a clean enum value instead of parsing free text.
    classifier = model.with_structured_output(RouteDecision)

    def router(state: SupportState) -> dict:
        messages = [SystemMessage(ROUTER_SYSTEM_PROMPT), *state["messages"]]
        decision: RouteDecision = classifier.invoke(messages)
        return {"route": decision.route}

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


def make_answer(model: BaseChatModel) -> Callable[[SupportState], dict]:
    """Build the small-talk node: a plain LLM reply, no tools."""

    def answer(state: SupportState) -> dict:
        messages = [SystemMessage(ANSWER_SYSTEM_PROMPT), *state["messages"]]
        reply = model.invoke(messages)
        return {"messages": [reply]}

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
    "Answer concisely, in the user's language, and use both the conversation "
    "history and your memories to stay consistent."
)


def make_support_model(
    model: BaseChatModel, tools: list[BaseTool]
) -> Callable[[SupportState], dict]:
    """Build the support node: the LLM step of the ReAct loop (LLM + tools).

    This node decides whether to answer or to call a tool. The `tools` node runs
    the calls, then loops back here — that back-and-forth IS the ReAct loop we
    were getting for free from `create_agent`, now made explicit.
    """
    model_with_tools = model.bind_tools(tools)

    def support_model(state: SupportState) -> dict:
        messages = [SystemMessage(SUPPORT_SYSTEM_PROMPT), *state["messages"]]
        reply = model_with_tools.invoke(messages)
        return {"messages": [reply]}

    return support_model


# --- Escalate (human handoff placeholder) ----------------------------------


def escalate(state: SupportState, runtime: Runtime[AgentContext]) -> dict:
    """Hand the conversation off to a human.

    Phase 6 placeholder: we just emit a transfer message. Phase 7 will replace
    this with a real human-in-the-loop `interrupt()` that pauses the graph.
    """
    user_id = runtime.context.user_id
    message = (
        "Je transfère votre demande à un conseiller humain, qui reprendra le fil "
        f"de cette conversation (référence client : {user_id}). Merci de patienter."
    )
    return {"messages": [AIMessage(content=message)]}
