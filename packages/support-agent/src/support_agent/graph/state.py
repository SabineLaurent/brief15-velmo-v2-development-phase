"""The graph state: the data that flows through the nodes.

`MessagesState` already gives us a `messages` channel with the right reducer
(new messages are appended, not overwritten). We only add `route`: the decision
made by the router node, which the conditional edge reads to pick a branch.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import MessagesState

Route = Literal["answer", "support", "escalate"]


class SupportState(MessagesState):
    """Conversation state: the message history + the last routing decision."""

    route: Route
    input_blocked: bool
    handled_by_human: bool
    summary: str
