"""Memory layer: short-term (conversation) + long-term (cross-session).

    short-term -> checkpointer, keyed by thread_id  (one conversation)
    long-term  -> store,        keyed by user_id    (across conversations)
    episodic   -> store,        keyed by NOTHING    (cases, shared across users)

Episodic memory is imported from `memory.episodic` directly rather than re-exported
here: it is not a per-customer memory, and blurring that distinction is what turns a
shared few-shot pool into a data leak. `memory.privacy` is likewise imported directly —
it is an operator surface and carries a `__main__`.
"""

from support_agent.memory.long_term import AgentContext, get_store, memories_namespace
from support_agent.memory.memory_tools import build_memory_tools
from support_agent.memory.short_term import get_checkpointer

__all__ = [
    "get_checkpointer",
    "get_store",
    "AgentContext",
    "build_memory_tools",
    "memories_namespace",
]
