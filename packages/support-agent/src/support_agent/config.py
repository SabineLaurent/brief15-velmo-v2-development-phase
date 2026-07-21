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
        # An empty line in `.env` (`LLM_TIMEOUT=`) means "not set", not "empty
        # string": fall back to the default below instead of feeding `""` to the
        # validator. Without this, optional numeric fields fail to parse and
        # optional string fields silently become `""` instead of `None`.
        env_ignore_empty=True,
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

    # --- Robustness (retries / timeout, applied in the LLM factory) ---
    # Passed through to the provider so a transient 429 / network blip is retried
    # instead of crashing the turn. `llm_timeout` is seconds (None = provider default).
    llm_max_retries: int = 3
    llm_timeout: float | None = None

    # --- Fallback provider (Phase 10, robustness) ---
    # If the PRIMARY provider is fully down (persistent 429, outage, dead key)
    # even after the retries above, transparently fall back to a SECOND provider.
    # Empty = no fallback (single provider). Same agnostic spirit as the primary:
    # it is just another (provider, model) pair, built through the same factory
    # path, and it must have its own credential set (section 2 of `.env`).
    llm_fallback_provider: str | None = None
    llm_fallback_model: str | None = None

    # --- Fast model role (latency: small -> strong cascade) ---
    # The router and the support node's tool-decision pass are easy LLM calls that
    # sit on the critical path BEFORE the first streamed token. Running them on a
    # small fast model (e.g. gpt-5.6-luna next to gpt-5.6-sol) shrinks the pre-roll
    # silence (time-to-first-token) while the strong model still writes the final,
    # streamed answer. See `docs/latence.md`. Empty = no cascade: every node uses
    # the primary model, exactly as before. `llm_fast_provider` defaults to the
    # primary provider, so a fast deployment on the SAME endpoint (same base_url /
    # api_key) needs only `llm_fast_model` to be set.
    llm_fast_provider: str | None = None
    llm_fast_model: str | None = None

    # --- Embeddings (Phase 4, RAG) ---
    embeddings_provider: str = "mistral"
    embeddings_model: str = "mistral-embed"

    # --- Persistence (durable memory) ---
    # One switch drives BOTH the checkpointer (short term) and the store (long
    # term), same agnostic spirit as the LLM provider. "memory" = in-RAM (lost on
    # restart, fine for demos); "sqlite" = durable on disk (survives a restart).
    persistence_backend: str = "memory"
    # Runtime state lives under TEMP/ (gitignored scratch), NOT under data/ —
    # data/ holds versioned source content (the FAQ), so keeping a database of
    # customer conversations out of it avoids ever committing one. Parent dirs
    # are created on connect (see memory/sqlite_conn.py).
    sqlite_path: str = "./TEMP/database/agent_state.db"

    # --- Guardrails (Phase 12, security) ---
    # `guardrails_enabled` is a kill switch: off = the graph is wired exactly as
    # before (START -> router), no overhead. `max_input_chars` caps the incoming
    # message length (cost / DoS) before it ever reaches the LLM.
    guardrails_enabled: bool = True
    guardrails_max_input_chars: int = 4000
    # Tool-boundary hardening (Phase 12-C): cap on LLM-generated tool fields
    # (ticket subject/body, memory text), and a rate limit on side-effecting
    # actions (ticket creation) — `limit` calls per `window` seconds, per customer.
    guardrails_max_tool_field_chars: int = 2000
    guardrails_action_rate_limit: int = 5
    guardrails_action_rate_window_s: float = 3600.0

    # --- Observability, LangSmith (Phase 2) ---
    langsmith_tracing: bool = False
    langsmith_project: str = "agnostic-support-agent"

    # --- Knowledge base (Phase 4) ---
    knowledge_dir: str = "./data/faq"
    # Where the persistent vector index lives (Chroma, embedded mode). Under
    # TEMP/ because the index is a rebuildable PROJECTION of `knowledge_dir`, not
    # source content — losing it costs one re-embedding, nothing more.
    knowledge_index_dir: str = "./TEMP/database/chroma"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
