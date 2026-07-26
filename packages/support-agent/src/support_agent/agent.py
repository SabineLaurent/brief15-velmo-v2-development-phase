"""Phase 7: the support agent with human-in-the-loop escalation.

Built on the Phase 6 `StateGraph`: a router node classifies the user's intent,
then a conditional edge dispatches to one of three branches (see
`support_agent.graph`):

    router  ->  answer    (small talk: plain LLM reply)
            ->  support   (FAQ + memory, explicit ReAct loop)
            ->  escalate  (human-in-the-loop: `interrupt()` pauses the graph)

Phase 7 adds the escalation flow: when the graph routes to `escalate`, it calls
`interrupt()` and pauses. `invoke` then returns a result carrying `__interrupt__`
instead of a final answer. We surface the case to a human operator, read their
reply, and resume the graph with `Command(resume=<reply>)` — which flows back to
the customer as the agent's message.

Memory is unchanged from Phase 5:

    checkpointer  ->  remembers THIS conversation   (keyed by thread_id)
    store         ->  remembers THIS customer       (keyed by user_id)

Run an interactive chat with:

    uv run python -m support_agent.agent
"""

from __future__ import annotations

import uuid

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from support_agent.config import get_settings
from support_agent.graph import build_support_graph
from support_agent.memory import AgentContext

# In a real app this comes from auth (the logged-in customer). For the tutorial
# demo we use a fixed id so long-term memory is easy to observe across threads.
DEFAULT_USER_ID = "demo-user"


def build_agent() -> CompiledStateGraph:
    """Assemble the agent: the explicit support graph (router + branches)."""
    return build_support_graph()


def main() -> None:
    settings = get_settings()
    agent = build_agent()

    # One thread_id per run = one continuous conversation the agent remembers.
    thread_id = str(uuid.uuid4())
    user_id = DEFAULT_USER_ID  # stable across threads/sessions for long-term memory

    print(f"[provider={settings.llm_provider} | model={settings.llm_model}]")
    print(f"[user_id={user_id} | thread_id={thread_id}]")
    print("Chat de support (tape 'quit' pour sortir)\n")

    while True:
        try:
            user_input = input("Vous   > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            break

        # Same context + config are reused on resume: the runtime `user_id` and,
        # crucially, the `thread_id` that lets the checkpointer find the paused run.
        context = AgentContext(user_id=user_id)  # long-term memory key
        config = {
            "configurable": {"thread_id": thread_id},  # short-term memory key
            "run_name": "support-chat",
            "tags": ["phase-7", f"provider:{settings.llm_provider}"],
            "metadata": {"model": settings.llm_model, "phase": "7-human-in-the-loop"},
        }

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            context=context,
            config=config,
        )

        # Human-in-the-loop console. Dormant today: `escalate` no longer pauses
        # the graph (it files a ticket and ends the turn — a pause with no
        # operator behind it killed the thread, see `make_escalate`). Kept
        # deliberately: this is the project's ONLY resume implementation, and it
        # is generic — it will drive the approval gate that `interrupt()` is
        # moving to. Delete it only if that plan is abandoned.
        while result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            print("\n--- ESCALADE : transfert à un conseiller humain ---")
            print(f"    client  : {payload['user_id']}")
            print(f"    demande : {payload['customer_message']}")
            operator_reply = input("Conseiller > ").strip()
            result = agent.invoke(
                Command(resume=operator_reply),
                context=context,
                config=config,
            )

        reply = result["messages"][-1].content
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
