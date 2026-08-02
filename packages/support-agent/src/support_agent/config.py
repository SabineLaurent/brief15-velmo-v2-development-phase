"""Typed application configuration, loaded from environment / `.env`.

Nothing here is hard-coded: the LLM provider, model and secrets all come from
the environment. This is the config half of the project's "agnostic" promise.
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Strongly-typed project settings, read from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    # --- LLM selection (the agnostic core) ---
    llm_provider: str = "mistral"
    llm_model: str = "mistral-large-latest"
    llm_temperature: float = 0.0

    # --- OpenAI-compatible endpoint (Azure OpenAI API v1, vLLM, third-party...) ---
    llm_inference_endpoint: str | None = None
    llm_inference_api_key: str | None = None

    # --- Robustness (retries / timeout, applied in the LLM factory) ---
    llm_max_retries: int = 3
    llm_timeout: float | None = None

    # --- Fallback provider (robustness) ---
    llm_fallback_provider: str | None = None
    llm_fallback_model: str | None = None

    # --- Fast model role (latency: small -> strong cascade) ---
    llm_fast_provider: str | None = None
    llm_fast_model: str | None = None

    # --- Embeddings (RAG) ---
    embeddings_provider: str = "mistral"
    embeddings_model: str = "mistral-embed"

    # --- Persistence (durable memory) ---
    persistence_backend: str = "memory"
    working_memory_db_path: str = "./database/working_memory/checkpoints.db"
    agent_memory_db_path: str = "./database/agent_memory/memories.db"

    database_url: str | None = None
    database_schema: str = "agent_state"

    # --- Long-term memory retention (GDPR) ---
    memory_ttl_days: float | None = 365.0
    memory_ttl_sweep_interval_minutes: int = 60

    # --- Context-window compaction (R4) ---
    compact_after_messages: int = 30
    compact_keep_last_messages: int = 10

    # --- Right to be forgotten (R5) ---
    forget_min_score: float = 0.35

    # --- Episodic memory ---
    episodic_memory_enabled: bool = True
    episodic_recall_limit: int = 2
    episodic_min_score: float = 0.35
    episodic_idle_minutes: float = 30.0

    # --- Guardrails (security) ---
    guardrails_enabled: bool = True
    guardrails_max_input_chars: int = 4000
    guardrails_max_tool_field_chars: int = 2000
    guardrails_action_rate_limit: int = 5
    guardrails_action_rate_window_s: float = 3600.0
    guardrails_owned_email_domains: str = "velmo.example"

    # --- Business backend (the `actions/` port) ---
    support_backend: str = "memory"
    shop_db_path: str = "./database/shop/shop.db"

    # --- Observability, LangSmith ---
    langsmith_tracing: bool = False
    langsmith_project: str = "agnostic-support-agent"

    # --- Evaluation tariff (MLOps) ---
    eval_price_per_1m_input_tokens: float | None = None
    eval_price_per_1m_output_tokens: float | None = None

    # --- Knowledge base ---
    knowledge_dir: str = "./data/kb-velmo"

    # --- HTTP exposure (deployment step 1) ---
    api_key: str | None = None
    api_allow_unauthenticated: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
