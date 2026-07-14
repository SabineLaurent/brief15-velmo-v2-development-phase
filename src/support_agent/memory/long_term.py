"""Long-term (cross-session) memory: the Store factory + runtime context.

Where the *checkpointer* (short-term) remembers ONE conversation, keyed by
`thread_id`, the *Store* (long-term) remembers a USER across conversations,
keyed by `user_id`. Different question, different tool:

    checkpointer  ->  "what did we say earlier in THIS chat?"   (thread_id)
    store         ->  "what do I know about THIS customer?"     (user_id)

The store organizes data by hierarchical *namespaces* (tuples), so one
customer's memories are physically separated from another's.

For now we use an in-memory store: perfect for learning, but state is lost when
the process exits. Swapping to a durable backend (SQLite / Postgres) later is a
one-line change *here* — the agent code never changes. Same agnostic idea as
the LLM and checkpointer factories.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from support_agent.llm.embeddings import get_embeddings


@dataclass
class AgentContext:
    """Runtime context passed at invoke time: identifies WHO we are talking to.

    This is how the agent's memory tools know under which `user_id` to read and
    write, so long-term memory stays scoped per customer.
    """

    user_id: str


def get_store() -> BaseStore:
    """Return the configured long-term memory store.

    Today: in-memory, with *semantic search* over stored memories — we reuse the
    same agnostic embeddings as the FAQ (Phase 4), so recall works by meaning,
    not exact keywords. Later we can select a persistent backend from config
    without touching any calling code.
    """
    embeddings = get_embeddings()
    # Probe once to learn the vector size instead of hard-coding a per-model
    # dimension — keeps the store provider-agnostic like everything else.
    dims = len(embeddings.embed_query("probe"))
    return InMemoryStore(
        index={"embed": embeddings, "dims": dims, "fields": ["text"]},
    )
