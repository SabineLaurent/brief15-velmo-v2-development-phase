"""Phase 1 demo entry point: talk to the LLM through the agnostic factory.

No memory, no RAG, no graph yet — just proof that we can reach *any* provider
through one abstract interface. Run with:

    uv run python -m support_agent.agent
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from support_agent.config import get_settings
from support_agent.llm import get_chat_model

SYSTEM_PROMPT = (
    "You are a helpful customer-support agent. Answer clearly and concisely, "
    "in the user's language."
)


def main() -> None:
    settings = get_settings()
    model = get_chat_model(settings)

    print(f"[provider={settings.llm_provider} | model={settings.llm_model}]\n")

    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage("Bonjour ! Présente-toi en une phrase en tant qu'agent de support."),
    ]

    # Enrich the LangSmith trace so runs are searchable/filterable in the
    # dashboard (by name, tags, or metadata). This is free observability hygiene.
    response = model.invoke(
        messages,
        config={
            "run_name": "support-demo",
            "tags": ["phase-2", f"provider:{settings.llm_provider}"],
            "metadata": {"model": settings.llm_model, "phase": "2-observability"},
        },
    )
    print(response.content)


if __name__ == "__main__":
    main()
