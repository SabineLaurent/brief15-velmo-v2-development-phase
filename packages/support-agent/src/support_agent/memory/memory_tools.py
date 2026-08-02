"""Agentic long-term memory: tools the agent calls to remember its users.

Same philosophy as agentic RAG: the model DECIDES when to act. Here it
decides when a fact is worth storing for the long run, and when to recall past
facts to stay consistent from one session to the next.

Memories are namespaced per user — `("memories", user_id)` — so one customer can
never read another's data. The `user_id` comes from the runtime context (see
`AgentContext`), not from the LLM, so the model cannot spoof it.
"""

from __future__ import annotations

import logging
import uuid

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from support_agent.guardrails import ToolGuard
from support_agent.memory.long_term import AgentContext, memories_namespace
from support_agent.memory.privacy import forget_user_memories

logger = logging.getLogger(__name__)

_namespace = memories_namespace


def build_memory_tools(
    tool_guard: ToolGuard | None = None, *, forget_min_score: float = 0.35
) -> list[BaseTool]:
    """Build the long-term memory tools: save, search, and FORGET.

    The third one makes the right to be forgotten (R5) reachable from the conversation
    itself; the operator-side surface (audit dump, full art. 17 erasure) lives in
    `memory/privacy.py`.

    Args:
        tool_guard: Optional hardening applied to `save_memory` before it PERSISTS
            (field validation + PII masking, so raw PII is never written to the
            durable store). `None` disables it.
        forget_min_score: the similarity a stored fact must reach before
            `forget_memory` may delete it. A knob rather than a constant, because
            cosine similarity is not comparable across embedding models.
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

        if tool_guard is not None:
            error = tool_guard.validate_field(text, field_name="memory")
            if error is not None:
                return error
            text = tool_guard.sanitize(text)

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

    @tool
    def forget_memory(what: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Permanently delete stored facts about the current user, on their request.

        Use this ONLY when the user asks you to forget something they told you
        ("forget my order number", "delete what you know about my address").
        Describe what to forget in `what`, using their own words. Never call this
        on your own initiative, and never to tidy up memory.

        Deletion is irreversible. Report back exactly what was deleted, and say
        so plainly when nothing matched — do not claim to have forgotten
        something you did not find.
        """
        store = runtime.store
        user_id = runtime.context.user_id

        try:
            deleted = forget_user_memories(
                store, user_id, what, min_score=forget_min_score
            )
        except RuntimeError as error:
            logger.exception("Unverified deletion for user=%s", user_id)
            return (
                "I could not confirm the deletion, so I will not claim it worked. "
                f"Please tell the customer it has been escalated. ({error})"
            )

        if not deleted:
            return (
                "Nothing close enough to that was stored, so nothing was deleted. "
                "Tell the customer you hold no such information rather than "
                "confirming a deletion."
            )
        lines = "\n".join(f"- {record.text}" for record in deleted)
        return f"Deleted {len(deleted)} memory(ies), verified gone:\n{lines}"

    return [save_memory, search_memories, forget_memory]
