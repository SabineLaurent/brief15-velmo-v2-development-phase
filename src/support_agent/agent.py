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
from support_agent.knowledge import build_faq_tool, build_vector_store
from support_agent.llm import get_chat_model
from support_agent.memory import get_checkpointer

SYSTEM_PROMPT = (
    "You are a helpful customer-support agent for an online store. "
    "For any factual question (orders, delivery, returns, refunds, payment, "
    "account, warranty...), ALWAYS call the `search_faq` tool first and answer "
    "ONLY from the retrieved content — never guess. Cite the source file you "
    "used (e.g. 'source : livraison.md'). If the FAQ does not contain the "
    "answer, say so honestly and suggest contacting a human agent. "
    "Answer concisely, in the user's language, and use the conversation history "
    "to stay consistent."
)


def build_agent() -> CompiledStateGraph:
    """Assemble the agent: agnostic LLM + FAQ retrieval tool + short-term memory."""
    model = get_chat_model()
    checkpointer = get_checkpointer()

    # RAG: build the FAQ knowledge base and expose it as a tool.
    vector_store = build_vector_store()
    faq_tool = build_faq_tool(vector_store)

    return create_agent(
        model,
        tools=[faq_tool],
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
                "tags": ["phase-4", f"provider:{settings.llm_provider}"],
                "metadata": {"model": settings.llm_model, "phase": "4-rag"},
            },
        )
        reply = result["messages"][-1].content
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
