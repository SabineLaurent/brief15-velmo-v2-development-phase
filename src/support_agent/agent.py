"""Phase 3: a stateful support agent with short-term (conversation) memory.

We move from a bare `model.invoke()` to a LangGraph agent built with LangChain's
`create_agent`, wired to a checkpointer. Same `thread_id` = same conversation,
so the agent remembers previous turns.

Run an interactive chat with:

    uv run python -m support_agent.agent
"""

from __future__ import annotations

import uuid

from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

from support_agent.config import get_settings
from support_agent.llm import get_chat_model
from support_agent.memory import get_checkpointer

SYSTEM_PROMPT = (
    "You are a helpful customer-support agent. Answer clearly and concisely, "
    "in the user's language. Use the conversation history to stay consistent."
)


def build_agent() -> CompiledStateGraph:
    """Assemble the agent: agnostic LLM + short-term memory checkpointer."""
    model = get_chat_model()
    checkpointer = get_checkpointer()
    return create_agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )


def main() -> None:
    settings = get_settings()
    agent = build_agent()

    # One thread_id per run = one continuous conversation the agent remembers.
    thread_id = str(uuid.uuid4())

    print(f"[provider={settings.llm_provider} | model={settings.llm_model}]")
    print(f"[thread_id={thread_id}]")
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
            config={
                "configurable": {"thread_id": thread_id},  # short-term memory key
                "run_name": "support-chat",
                "tags": ["phase-3", f"provider:{settings.llm_provider}"],
                "metadata": {"model": settings.llm_model, "phase": "3-memory"},
            },
        )
        reply = result["messages"][-1].content
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
