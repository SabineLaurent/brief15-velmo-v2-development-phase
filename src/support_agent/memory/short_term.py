"""Short-term (conversation) memory: the checkpointer factory.

A *checkpointer* persists the agent's state after every step, keyed by
`thread_id`. Replaying the same `thread_id` continues the same conversation —
that is what "the agent remembers the discussion" concretely means.

For now we use an in-memory saver: perfect for learning, but state is lost when
the process exits. Swapping to a durable backend (SQLite / Postgres) later is a
one-line change *here* — the agent code never changes. Same agnostic idea as the
LLM factory.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


def get_checkpointer() -> BaseCheckpointSaver:
    """Return the configured short-term memory checkpointer.

    Today: in-memory (debug/learning). Later we can select a persistent backend
    from config without touching any calling code.
    """
    return InMemorySaver()
