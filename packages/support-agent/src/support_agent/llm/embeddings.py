"""Provider-agnostic embeddings factory — the RAG counterpart of the LLM factory.

Embeddings turn text into vectors so we can measure semantic similarity (the
core trick of retrieval). Like the chat model, the embeddings provider is a
config choice, not hard-coded.
"""

from __future__ import annotations

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from support_agent.config import Settings, get_settings
from support_agent.llm._extras import provider_package_required

_PROVIDER_ALIASES: dict[str, str] = {
    "mistral": "mistralai",
    "openai": "openai",
    "google_genai": "google_genai",
}


def get_embeddings(settings: Settings | None = None) -> Embeddings:
    """Build the configured embeddings model as an abstract `Embeddings`."""
    settings = settings or get_settings()
    provider = settings.embeddings_provider.lower()

    if provider == "openai_compatible":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embeddings_model,
            base_url=settings.llm_inference_endpoint,
            api_key=settings.llm_inference_api_key,
        )

    provider_id = _PROVIDER_ALIASES.get(provider, provider)
    with provider_package_required(provider):
        return init_embeddings(f"{provider_id}:{settings.embeddings_model}")
