"""Typed application configuration, loaded from environment / `.env`.

Nothing here is hard-coded: the LLM provider, model and secrets all come from
the environment. This is the config half of the project's "agnostic" promise.
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Populate os.environ from `.env` so provider SDKs (which auto-discover their
# own *_API_KEY variables) can find their credentials. Pydantic reads the same
# file below for our own typed settings.
load_dotenv()


class Settings(BaseSettings):
    """Strongly-typed project settings, read from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM selection (the agnostic core) ---
    llm_provider: str = "mistral"
    llm_model: str = "mistral-large-latest"
    llm_temperature: float = 0.0

    # --- OpenAI-compatible endpoint (Azure OpenAI API v1, vLLM, third-party...) ---
    # The single generic path for any endpoint speaking the OpenAI API: its URL
    # and key. Used only by provider "openai_compatible" (hosted providers like
    # mistral/groq bring their own MISTRAL_API_KEY / GROQ_API_KEY instead).
    llm_inference_endpoint: str | None = None
    llm_inference_api_key: str | None = None

    # --- Embeddings (Phase 4, RAG) ---
    embeddings_provider: str = "mistral"
    embeddings_model: str = "mistral-embed"

    # --- Observability, LangSmith (Phase 2) ---
    langsmith_tracing: bool = False
    langsmith_project: str = "agnostic-support-agent"

    # --- Knowledge base (Phase 4) ---
    knowledge_dir: str = "./data/faq"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
