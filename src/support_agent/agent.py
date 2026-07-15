"""Phase 6: a support agent orchestrated by an explicit LangGraph `StateGraph`.

We stop using the prebuilt `create_agent` and "open the hood": a router node
classifies the user's intent, then a conditional edge dispatches to one of three
branches (see `support_agent.graph`):

    router  ->  answer    (small talk: plain LLM reply)
            ->  support   (FAQ + memory, explicit ReAct loop)
            ->  escalate  (human handoff; real interrupt comes in Phase 7)

Memory is unchanged from Phase 5:

    checkpointer  ->  remembers THIS conversation   (keyed by thread_id)
    store         ->  remembers THIS customer       (keyed by user_id)

Run an interactive chat with:

    uv run python -m support_agent.agent
"""

from __future__ import annotations

import uuid

from langgraph.graph.state import CompiledStateGraph

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

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            context=AgentContext(user_id=user_id),  # long-term memory key
            config={
                "configurable": {"thread_id": thread_id},  # short-term memory key
                "run_name": "support-chat",
                "tags": ["phase-6", f"provider:{settings.llm_provider}"],
                "metadata": {"model": settings.llm_model, "phase": "6-orchestration"},
            },
        )
        reply = result["messages"][-1].content
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
