"""The API seam: the one stable door out of the agent.

`stream_reply` yields plain reply chunks and never exposes a lang* object, so a front
end stays interchangeable. It delivers the graph's TERMINAL state rather than live
tokens, because the output guard runs as a later node and must inspect the complete
reply. It bridges sync to async in a worker thread so the checkpointer can stay
synchronous. Every exit path yields exactly one non-empty chunk.

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

    Building it is expensive (embeddings probe, vector store, SQLite setup), and the
    checkpointer and store hold the per-conversation state anyway.
    """
    return build_support_graph()


def _reply_from_result(result: dict) -> str:
    """Extract the one customer-facing reply from the graph's terminal result.

    Reads the final STATE, never a node name, so rewiring the graph cannot silently
    break it. Order matters: an interrupted run still carries the previous turn's
    messages, so the pause must be detected before `messages` is read.
    """
    if result.get("__interrupt__"):
        return ESCALATION_PENDING_MESSAGE

    messages = result.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.text:
        return last.text

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

    Args:
        message: The customer's message.
        user_id: Long-term memory key (per-customer isolation in the store).
        thread_id: Short-term memory key; reusing it continues the conversation.

    Yields:
        The customer-facing reply — today exactly one guarded chunk. Consumers must
        still treat it as a stream and concatenate what they receive.
    """
    agent = get_agent()
    settings = get_settings()

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
            logger.exception("stream_reply graph run failed for thread_id=%s.", thread_id)
            return GRACEFUL_ERROR_MESSAGE
        return _reply_from_result(result)

    yield await asyncio.to_thread(_run)


async def _smoke() -> None:
    """Tiny no-front demo: two messages on the same `thread_id`."""
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
