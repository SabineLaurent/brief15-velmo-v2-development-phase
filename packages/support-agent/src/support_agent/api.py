"""The API seam (Phase B1.1): the ONE stable door out of the agent.

This module is the contract between the brain (LangGraph, hidden) and ANY front
end. A front — Chainlit today, React tomorrow — calls `stream_reply(...)` and
gets an async stream of reply tokens. It never sees a graph, a node, a state or
any other lang* object. That is the whole point: the front stays interchangeable
because it only ever knows this function.

    front  ──►  stream_reply(message, *, user_id, thread_id)  ──►  [ graph hidden ]
                ↑ THE SEAM (the contract)

Three design choices, each deliberate:

1. Filter by NODE, not by content. The graph has three LLM nodes (`router`,
   `answer`, `model`). Under `stream_mode="messages"` every token arrives tagged
   with its `langgraph_node`. We only forward tokens from the two customer-facing
   nodes — never the router's internal routing decision. This filtering lives
   HERE, in the seam, so no front ever has to know the graph's shape.

2. Bridge sync → async in a worker thread instead of using `astream`. Our
   checkpointers are synchronous (`InMemorySaver` / `SqliteSaver`); `astream`
   would demand an async saver (`AsyncSqliteSaver`) and silently break the
   `sqlite` backend — breaking the project's agnostic promise ("switch backend =
   one env var"). So we drive the sync `.stream()` from a thread and pump tokens
   into an `asyncio.Queue`. The front gets a clean async API; the thread absorbs
   the sync/async boundary.

3. Always deliver a reply. Some replies are NOT token-streamed (a message blocked
   by the input guard, a graceful-degradation fallback, a human escalation reply):
   they are injected whole into state instead of coming from a streaming LLM call.
   If nothing streamed, we read the final state and yield the last message in one
   shot — so the UI never shows an empty answer.

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
from support_agent.memory import AgentContext

logger = logging.getLogger(__name__)

# The two nodes whose streamed LLM tokens ARE the customer's reply. Everything
# else — the `router` (structured routing decision), the `tools` node, the
# guard nodes — is internal and must never leak into the front's stream.
CUSTOMER_FACING_NODES = frozenset({"answer", "model"})


@lru_cache(maxsize=1)
def get_agent() -> CompiledStateGraph:
    """Build (once) and cache the compiled support graph.

    Building the graph is expensive (embeddings probe, vector store, SQLite
    setup), so we do it a single time per process and reuse it across calls —
    the checkpointer/store keep per-conversation and per-customer state anyway.
    """
    return build_support_graph()


async def stream_reply(
    message: str,
    *,
    user_id: str,
    thread_id: str,
) -> AsyncIterator[str]:
    """Stream the agent's reply to one customer message, token by token.

    This is THE seam. It hides the graph entirely and yields plain strings.

    Args:
        message: The customer's message.
        user_id: WHO we are talking to — the long-term memory key (per-customer
            isolation in the store). In the demo it is simulated (no auth yet).
        thread_id: WHICH conversation this is — the short-term memory key. Reusing
            the same `thread_id` continues the same remembered conversation.

    Yields:
        Chunks of the final customer-facing reply, in order. Normally these are
        LLM tokens as they are generated; when the reply is injected whole
        (guard block, graceful error, escalation) the whole reply is yielded once.
    """
    agent = get_agent()
    settings = get_settings()

    # `context` carries the runtime identity (long-term memory); `config` carries
    # the short-term memory key + LangSmith labels for this streamed turn.
    context = AgentContext(user_id=user_id)
    config = {
        "configurable": {"thread_id": thread_id},
        "run_name": "support-stream",
        "tags": ["scope-b", "b1.1", f"provider:{settings.llm_provider}"],
        "metadata": {"model": settings.llm_model, "user_id": user_id},
    }
    inputs = {"messages": [{"role": "user", "content": message}]}

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | object] = asyncio.Queue()
    done = object()  # sentinel: the producer thread has finished

    def _pump() -> None:
        """Run the SYNC graph stream in a worker thread; push tokens to the queue.

        `call_soon_threadsafe` is the safe hand-off from this thread back to the
        event loop that owns the queue.
        """
        streamed = False
        try:
            for chunk, meta in agent.stream(
                inputs,
                context=context,
                config=config,
                stream_mode="messages",
            ):
                # Keep only tokens produced by the customer-facing LLM nodes.
                if meta.get("langgraph_node") not in CUSTOMER_FACING_NODES:
                    continue
                text = getattr(chunk, "content", "")
                # Content can be empty (e.g. a tool-call step) or, on some
                # providers, a list of content blocks; we only forward plain text.
                if isinstance(text, str) and text:
                    streamed = True
                    loop.call_soon_threadsafe(queue.put_nowait, text)

            if not streamed:
                # No LLM tokens for this turn: the reply was injected whole
                # (input blocked, graceful error, escalation). Deliver it too, so
                # the UI never renders an empty answer.
                snapshot = agent.get_state(config)
                messages = snapshot.values.get("messages", [])
                last = messages[-1] if messages else None
                if isinstance(last, AIMessage) and isinstance(last.content, str) and last.content:
                    loop.call_soon_threadsafe(queue.put_nowait, last.content)
        except Exception:
            # Never wedge the consumer: log and let the sentinel end the stream.
            logger.exception("stream_reply producer failed.")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    future = loop.run_in_executor(None, _pump)
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            yield item  # type: ignore[misc]  # never the sentinel here
    finally:
        # Wait for the worker thread to finish (and surface any late failure).
        await future


async def _smoke() -> None:
    """Tiny no-front demo: iterate the generator and print tokens as they land.

    Two messages on the SAME thread_id, to show the seam carries short-term
    memory across turns just like the CLI agent does.
    """
    import uuid

    thread_id = str(uuid.uuid4())
    user_id = "smoke-user"
    for message in ["Bonjour !", "Quels sont vos délais de livraison ?"]:
        print(f"\nVous  > {message}\nAgent > ", end="", flush=True)
        async for token in stream_reply(message, user_id=user_id, thread_id=thread_id):
            print(token, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(_smoke())
