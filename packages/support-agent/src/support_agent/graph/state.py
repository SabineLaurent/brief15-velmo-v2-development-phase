"""The graph state: the data that flows through the nodes (Phase 6).

`MessagesState` already gives us a `messages` channel with the right reducer
(new messages are appended, not overwritten). We only add `route`: the decision
made by the router node, which the conditional edge reads to pick a branch.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import MessagesState

# The three branches the router can choose. Kept as a type alias so the router,
# the state and the conditional edge all agree on the exact same set of values.
Route = Literal["answer", "support", "escalate"]


class SupportState(MessagesState):
    """Conversation state: the message history + the last routing decision."""

    route: Route
    # Set by the `guard_input` node (Phase 12-A): True when the entry guard
    # refused the message, so the entry conditional edge short-circuits to END.
    input_blocked: bool
    # Set by `escalate`: a human now owns this case, so the bot must stop
    # answering on this thread. This is what support platforms call an "agent
    # takeover", and the word matters: the conversation is NOT paused, it is
    # REASSIGNED. The customer keeps writing into the same thread; the bot simply
    # stays quiet instead of blocking the graph (see docs/escalade.md).
    handled_by_human: bool
