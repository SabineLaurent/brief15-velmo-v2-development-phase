"""The API seam (Phase B1.1): the ONE stable door out of the agent.

This module is the contract between the brain (LangGraph, hidden) and ANY front
end. A front — Chainlit today, React tomorrow — calls `stream_reply(...)` and
gets an async stream of reply chunks. It never sees a graph, a node, a state or
any other lang* object. That is the whole point: the front stays interchangeable
because it only ever knows this function.

    front  ──►  stream_reply(message, *, user_id, thread_id)  ──►  [ graph hidden ]
                ↑ THE SEAM (the contract)

Three design choices, each deliberate:

1. Deliver the TERMINAL result, not the LLM's tokens. Earlier this seam forwarded
   tokens live from the `answer` / `model` nodes. That was wrong: the output guard
   (`guard_output`, Phase 12-B) runs as a LATER node, so anything it redacts or
   replaces had ALREADY been shown to the customer — the guardrail protected the
   checkpoint and nothing else. It also leaked the ReAct loop's internal
   tool-decision preamble ("Je vais consulter la FAQ…") into the answer, and it
   delivered nothing at all on the paths that never stream (escalation).
   We now read the graph's terminal state and deliver the ONE message the graph
   actually decided to send.

   The cost is real and accepted: the reply lands in one chunk, so TTFT == total
   response time. That is not an implementation gap — the output guard inspects
   the COMPLETE reply (it can replace one that echoes the system prompt), so it
   cannot clear tokens as they arrive. Guarding and token-streaming are mutually
   exclusive here, and correctness wins. See `docs/streaming.md`.

2. Bridge sync → async in a worker thread. Our checkpointers are synchronous
   (`InMemorySaver` / `SqliteSaver`); going async would demand an async saver
   (`AsyncSqliteSaver`) and silently break the `sqlite` backend — breaking the
   project's agnostic promise ("switch backend = one env var"). So we run the
   sync graph in a thread and hand the result back to the event loop.

3. ALWAYS deliver a reply — for real this time. Every exit path is covered: the
   normal reply, the guard-blocked reply, a graceful message when the run itself
   crashes, and a handoff notice when the graph pauses on `escalate`. The seam
   never completes without yielding exactly one non-empty chunk, so no front can
   render an empty answer.

The signature stays an async generator on purpose: it is the stable contract. A
future non-guarded or step-by-step mode (Phase B1.5) can yield more chunks
without touching a single front end.

Try it without any front:

    uv run python -m support_agent.api
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from support_agent.config import get_settings
from support_agent.graph import build_support_graph
from support_agent.graph.nodes import (
    ESCALATION_PENDING_MESSAGE,
    GRACEFUL_ERROR_MESSAGE,
)
from support_agent.memory import AgentContext

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_agent() -> CompiledStateGraph:
    """Build (once) and cache the compiled support graph.

    Building the graph is expensive (embeddings probe, vector store, SQLite
    setup), so we do it a single time per process and reuse it across calls —
    the checkpointer/store keep per-conversation and per-customer state anyway.
    """
    return build_support_graph()


def _reply_from_result(result: dict) -> str:
    """Extract the ONE customer-facing reply from the graph's terminal result.

    Shape-agnostic on purpose: we read the final STATE, never a node name. The
    seam therefore knows nothing about the graph's topology — rewiring branches,
    renaming nodes or adding a guard cannot silently break it.

    Order matters. An interrupted run still carries the previous turn's messages,
    so the pause MUST be detected before we look at `messages` — otherwise we
    would replay a stale reply as if it were this turn's answer.
    """
    # The graph is paused on `escalate`, waiting for a human (`interrupt()`). No
    # AI reply exists yet: the human writes it on resume. Tell the customer.
    if result.get("__interrupt__"):
        return ESCALATION_PENDING_MESSAGE

    messages = result.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and isinstance(last.content, str) and last.content:
        return last.content

    # Should not happen: every branch ends by appending an AIMessage. If it does,
    # the customer still gets something coherent instead of an empty bubble.
    logger.error(
        "Graph terminated with no customer-facing reply (last=%r).", type(last).__name__
    )
    return GRACEFUL_ERROR_MESSAGE


async def stream_reply(
    message: str,
    *,
    user_id: str,
    thread_id: str,
) -> AsyncIterator[str]:
    """Stream the agent's reply to one customer message.

    This is THE seam. It hides the graph entirely and yields plain strings.

    Args:
        message: The customer's message.
        user_id: WHO we are talking to — the long-term memory key (per-customer
            isolation in the store). In the demo it is simulated (no auth yet).
        thread_id: WHICH conversation this is — the short-term memory key. Reusing
            the same `thread_id` continues the same remembered conversation.

    Yields:
        The customer-facing reply. Today that is exactly ONE chunk: the guarded
        final message (see design choice 1 — the output guard needs the whole
        reply, so nothing can be released early). Consumers must still treat this
        as a stream and concatenate what they receive.
    """
    agent = get_agent()
    settings = get_settings()

    # `context` carries the runtime identity (long-term memory); `config` carries
    # the short-term memory key + LangSmith labels for this turn.
    context = AgentContext(user_id=user_id)
    config = {
        "configurable": {"thread_id": thread_id},
        "run_name": "support-stream",
        "tags": ["scope-b", "b1.1", f"provider:{settings.llm_provider}"],
        "metadata": {"model": settings.llm_model, "user_id": user_id},
    }
    inputs = {"messages": [{"role": "user", "content": message}]}

    def _run() -> str:
        """Run the SYNC graph to completion in a worker thread; return the reply."""
        try:
            result = agent.invoke(inputs, context=context, config=config)
        except Exception:
            # The nodes already degrade gracefully on their own LLM failures, so
            # reaching here means the graph INFRASTRUCTURE broke (checkpointer,
            # store, wiring). Log it loudly, but never hand the front an empty
            # stream or a stack trace — it has no way to render either.
            logger.exception("stream_reply graph run failed for thread_id=%s.", thread_id)
            return GRACEFUL_ERROR_MESSAGE
        return _reply_from_result(result)

    yield await asyncio.to_thread(_run)


async def _smoke() -> None:
    """Tiny no-front demo: iterate the generator and print what the seam delivers.

    Two messages on the SAME thread_id, to show the seam carries short-term
    memory across turns just like the CLI agent does.
    """
    import uuid

    thread_id = str(uuid.uuid4())
    user_id = "smoke-user"
    for message in ["Bonjour !", "Quels sont vos délais de livraison ?"]:
        print(f"\nYou   > {message}\nAgent > ", end="", flush=True)
        async for chunk in stream_reply(message, user_id=user_id, thread_id=thread_id):
            print(chunk, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(_smoke())
