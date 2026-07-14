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
    "azure_openai": "azure_openai",
}


def get_embeddings(settings: Settings | None = None) -> Embeddings:
    """Build the configured embeddings model as an abstract `Embeddings`."""
    settings = settings or get_settings()
    provider = settings.embeddings_provider.lower()
    provider_id = _PROVIDER_ALIASES.get(provider, provider)
    return init_embeddings(f"{provider_id}:{settings.embeddings_model}")
