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
    # Runtime state lives under database/ (gitignored), NOT under data/ — data/
    # holds versioned source content (the FAQ), so keeping a database of customer
    # conversations out of it avoids ever committing one. Parent dirs are created
    # on connect (see memory/sqlite_conn.py).
    #
    # Two files, one per memory horizon, so each is readable/deletable on its own:
    #   working memory -> the thread being executed (keyed by thread_id)
    #   agent memory   -> what we know about a customer (keyed by user_id)
    # Postgres collapses both back into a single database; that is expected — the
    # split is a development-time affordance, not an architectural boundary.
    #
    # Named for the ROLE, not the engine, and suffixed `_PATH` because that is what
    # they hold: a filesystem path handed straight to sqlite3.connect(). Postgres
    # gets its own `database_url` below (a connection string is a different kind
    # of value); `persistence_backend` picks which one is read.
    working_memory_db_path: str = "./database/working_memory/checkpoints.db"
    agent_memory_db_path: str = "./database/agent_memory/memories.db"

    # Read only when `persistence_backend == "postgres"`. Deliberately has NO
    # default: a wrong-but-plausible fallback like "localhost:5432" would let the
    # app start and fail later, deep inside a request. Missing means missing, and
    # the memory factories say so at boot.
    #
    # ONE url for both horizons, on purpose: unlike SQLite (two files, so each is
    # readable and deletable on its own), Postgres holds working memory and agent
    # memory in the SAME database — separated by SCHEMA, not by server. Semantic
    # search over memories runs in that same database through pgvector. See
    # docs/architecture-cible-2026-07-25.md §3.
    database_url: str | None = None
    # The Postgres schema our state lives in (created on connect). Keeping it out
    # of `public` means a `\dt` shows OUR tables, and a future `knowledge` schema
    # for the FAQ index can sit next to it without collision.
    database_schema: str = "agent_state"

    # --- Long-term memory retention (GDPR) ---
    # Agent memory holds personal data, so it must expire. The Postgres store runs
    # a background *sweeper* that deletes rows past their TTL — the concrete
    # answer to "right to be forgotten" for what the agent remembers on its own.
    # `None` = keep forever (the SQLite/in-memory behaviour, unchanged).
    memory_ttl_days: float | None = 365.0
    memory_ttl_sweep_interval_minutes: int = 60

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
    knowledge_dir: str = "./data/kb-velmo"

    # --- HTTP exposure (deployment step 1) ---
    # The SERVICE-level key: proves the CALLER is allowed to use this agent at all.
    # It is NOT per-customer identity (that is `user_id`, and proving it is step 5).
    # Its first job is boring but vital: an open endpoint wired to a paid LLM key
    # gets its credit drained overnight.
    #
    # Fail-closed by design: `server.py` REFUSES TO START when no key is set,
    # unless `api_allow_unauthenticated` is explicitly true. Making the insecure
    # mode opt-in is what stops the classic accident — deploying with the variable
    # forgotten and never noticing the door is open.
    api_key: str | None = None
    api_allow_unauthenticated: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
