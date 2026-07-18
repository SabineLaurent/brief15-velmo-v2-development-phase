"""Agentic long-term memory: tools the agent calls to remember its users.

Same philosophy as agentic RAG (Phase 4): the model DECIDES when to act. Here it
decides when a fact is worth storing for the long run, and when to recall past
facts to stay consistent from one session to the next.

Memories are namespaced per user — `("memories", user_id)` — so one customer can
never read another's data. The `user_id` comes from the runtime context (see
`AgentContext`), not from the LLM, so the model cannot spoof it.
"""

from __future__ import annotations

import uuid

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from support_agent.guardrails import ToolGuard
from support_agent.memory.long_term import AgentContext


def _namespace(user_id: str) -> tuple[str, str]:
    """The per-user memory namespace. Isolation happens right here."""
    return ("memories", user_id)


def build_memory_tools(tool_guard: ToolGuard | None = None) -> list[BaseTool]:
    """Build the `save_memory` / `search_memories` long-term memory tools.

    Args:
        tool_guard: Optional Phase 12-C hardening applied to `save_memory` before
            it PERSISTS (field validation + PII masking, so raw PII is never
            written to the durable store). `None` disables it.
    """

    @tool
    def save_memory(text: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Save a durable fact about the current user for future conversations.

        Use this whenever the user shares something worth remembering long-term:
        their name, preferences (language, contact channel), recurring problems,
        or lasting context about their account or orders. Store one concise,
        self-contained fact per call (e.g. "Prefers to be contacted in French").
        """
        store = runtime.store
        user_id = runtime.context.user_id

        # Phase 12-C: validate and strip PII BEFORE persisting to the durable store.
        if tool_guard is not None:
            error = tool_guard.validate_field(text, field_name="memory")
            if error is not None:
                return error
            text = tool_guard.sanitize(text)

        # Random key: each memory is a new entry, we never overwrite blindly.
        store.put(_namespace(user_id), str(uuid.uuid4()), {"text": text})
        return f"Saved memory: {text}"

    @tool
    def search_memories(query: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Recall durable facts about the current user from past conversations.

        Use this at the start of a conversation, or whenever the user refers to
        something they told you before (their name, preferences, past orders),
        so you stay consistent across sessions. Returns the most relevant stored
        memories for this user.
        """
        store = runtime.store
        user_id = runtime.context.user_id
        results = store.search(_namespace(user_id), query=query, limit=5)
        if not results:
            return "No stored memory for this user yet."
        return "\n".join(f"- {item.value['text']}" for item in results)

    return [save_memory, search_memories]
