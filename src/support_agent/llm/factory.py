"""Provider-agnostic chat model factory — the heart of the project.

The rest of the application never instantiates a provider directly. It calls
`get_chat_model()` and receives a `BaseChatModel`. Switching from Mistral to
Groq, Google, Azure AI Foundry or a self-hosted API is a `.env` change, not a
code change.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from support_agent.config import Settings, get_settings

# Map our friendly `LLM_PROVIDER` values to the `model_provider` identifiers
# understood by LangChain's `init_chat_model`. Add a line here to support a new
# first-class provider — nothing else in the app needs to change.
_PROVIDER_ALIASES: dict[str, str] = {
    "mistral": "mistralai",
    "groq": "groq",
    "google_genai": "google_genai",
    "openai": "openai",
    "azure_openai": "azure_openai",
    "azure_ai": "azure_ai",  # Azure AI Foundry (Azure AI Inference)
}


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Build the configured chat model as an abstract `BaseChatModel`.

    Args:
        settings: Optional settings override (handy for tests). Defaults to the
            process-wide cached settings.

    Returns:
        A ready-to-use chat model exposing the standard LangChain interface
        (`.invoke()`, `.stream()`, tool binding, ...).
    """
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    # Case 1 — first-class provider: delegate to init_chat_model. Credentials are
    # auto-discovered from standard env vars (MISTRAL_API_KEY, GROQ_API_KEY, ...).
    if provider in _PROVIDER_ALIASES:
        return init_chat_model(
            model=settings.llm_model,
            model_provider=_PROVIDER_ALIASES[provider],
            temperature=settings.llm_temperature,
        )

    # Case 2 — any OpenAI-compatible endpoint (self-hosted vLLM/Ollama, a third
    # party, or Foundry exposed as OpenAI). Just point base_url at it.
    if provider == "openai_compatible":
        return init_chat_model(
            model=settings.llm_model,
            model_provider="openai",
            temperature=settings.llm_temperature,
            base_url=settings.custom_llm_base_url,
            api_key=settings.custom_llm_api_key,
        )

    # Case 3 — a fully custom, non-standard API. When you need it, implement a
    # ~30-line BaseChatModel subclass in `llm/adapters/custom.py` and return it
    # here. This is the ONLY place that would know your API's details.
    if provider == "custom":
        raise NotImplementedError(
            "Custom provider not implemented yet. Add an adapter in "
            "support_agent/llm/adapters/custom.py (see docs/architecture.md §2)."
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER={settings.llm_provider!r}. "
        f"Expected one of: {', '.join(sorted(_PROVIDER_ALIASES))}, "
        f"openai_compatible, custom."
    )
