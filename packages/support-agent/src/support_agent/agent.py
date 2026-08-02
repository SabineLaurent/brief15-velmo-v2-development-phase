"""Interactive CLI for the support agent.

A router node classifies the intent, then a conditional edge dispatches to `answer`
(small talk), `support` (FAQ + memory, ReAct loop) or `escalate`. Escalation calls
`interrupt()`: `invoke` returns `__interrupt__` instead of an answer, a human operator
replies, and the graph resumes with `Command(resume=…)`.

    uv run python -m support_agent.agent
"""

from __future__ import annotations

import uuid

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from support_agent.config import get_settings
from support_agent.graph import build_support_graph
from support_agent.memory import AgentContext

DEFAULT_USER_ID = "demo-user"


def build_agent() -> CompiledStateGraph:
    """Assemble the agent: the explicit support graph (router + branches)."""
    return build_support_graph()


def main() -> None:
    settings = get_settings()
    agent = build_agent()

    thread_id = str(uuid.uuid4())
    user_id = DEFAULT_USER_ID

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

        context = AgentContext(user_id=user_id)
        config = {
            "configurable": {"thread_id": thread_id},
            "run_name": "support-chat",
            "tags": ["phase-7", f"provider:{settings.llm_provider}"],
            "metadata": {"model": settings.llm_model, "phase": "7-human-in-the-loop"},
        }

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            context=context,
            config=config,
        )

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

        reply = result["messages"][-1].text
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
