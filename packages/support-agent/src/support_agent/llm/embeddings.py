"""Provider-agnostic embeddings factory — the RAG counterpart of the LLM factory.

Embeddings turn text into vectors so we can measure semantic similarity (the
core trick of retrieval). Like the chat model, the embeddings provider is a
config choice, not hard-coded.
"""

from __future__ import annotations

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from support_agent.config import Settings, get_settings

# Same idea as the chat model factory: map friendly names to LangChain providers.
_PROVIDER_ALIASES: dict[str, str] = {
    "mistral": "mistralai",
    "openai": "openai",
    "google_genai": "google_genai",
}


def get_embeddings(settings: Settings | None = None) -> Embeddings:
    """Build the configured embeddings model as an abstract `Embeddings`."""
    settings = settings or get_settings()
    provider = settings.embeddings_provider.lower()

    # OpenAI-compatible endpoint (Azure OpenAI API v1, vLLM, a third party...):
    # same rail as the chat factory's `openai_compatible` branch — just point
    # base_url at it. Reuses the chat model's endpoint + key, so embeddings live
    # on the same Azure resource. `embeddings_model` is the deployment name.
    if provider == "openai_compatible":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embeddings_model,
            base_url=settings.llm_inference_endpoint,
            api_key=settings.llm_inference_api_key,
        )

    # First-class hosted providers: delegate to init_embeddings, which
    # auto-discovers each provider's credentials from standard env vars.
    provider_id = _PROVIDER_ALIASES.get(provider, provider)
    return init_embeddings(f"{provider_id}:{settings.embeddings_model}")
