"""Phase 5: a support agent with short-term AND long-term memory.

We build on Phase 3/4 (LangChain `create_agent` + checkpointer + agentic RAG)
and add cross-session memory via a LangGraph `store`:

    checkpointer  ->  remembers THIS conversation   (keyed by thread_id)
    store         ->  remembers THIS customer       (keyed by user_id)

The agent gets two new tools (`save_memory` / `search_memories`) and decides on
its own when to remember a durable fact and when to recall it — the same
agentic pattern as the FAQ search.

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
from support_agent.memory import (
    AgentContext,
    build_memory_tools,
    get_checkpointer,
    get_store,
)

# In a real app this comes from auth (the logged-in customer). For the tutorial
# demo we use a fixed id so long-term memory is easy to observe across threads.
DEFAULT_USER_ID = "demo-user"

SYSTEM_PROMPT = (
    "You are a helpful customer-support agent for an online store. "
    "For any factual question (orders, delivery, returns, refunds, payment, "
    "account, warranty...), ALWAYS call the `search_faq` tool first and answer "
    "ONLY from the retrieved content — never guess. Cite the source file you "
    "used (e.g. 'source : livraison.md'). If the FAQ does not contain the "
    "answer, say so honestly and suggest contacting a human agent. "
    "You also have a long-term memory about the current customer: call "
    "`search_memories` when the user refers to something they told you before "
    "(their name, preferences, past orders), and call `save_memory` when they "
    "share a durable fact worth remembering across sessions. "
    "Answer concisely, in the user's language, and use both the conversation "
    "history and your memories to stay consistent."
)


def build_agent() -> CompiledStateGraph:
    """Assemble the agent: agnostic LLM + FAQ tool + short- and long-term memory."""
    model = get_chat_model()
    checkpointer = get_checkpointer()  # short-term: this conversation
    store = get_store()  # long-term: this customer, across conversations

    # RAG: build the FAQ knowledge base and expose it as a tool.
    vector_store = build_vector_store()
    faq_tool = build_faq_tool(vector_store)

    tools = [faq_tool, *build_memory_tools()]

    return create_agent(
        model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        store=store,
        context_schema=AgentContext,
    )


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
                "tags": ["phase-5", f"provider:{settings.llm_provider}"],
                "metadata": {"model": settings.llm_model, "phase": "5-long-term-memory"},
            },
        )
        reply = result["messages"][-1].content
        print(f"Agent  > {reply}\n")


if __name__ == "__main__":
    main()
