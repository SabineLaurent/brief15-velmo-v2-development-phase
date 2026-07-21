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
    "azure_ai": "azure_ai",  # Azure AI Foundry native models (Azure AI Inference)
}

# Exceptions that trigger a fallback to the secondary provider. Kept broad ON
# PURPOSE: provider SDKs raise their OWN exception types (that is the whole point
# of being agnostic), so we cannot enumerate them here. Transient blips are
# already absorbed by `max_retries`; this net is for "the primary is really down"
# (persistent 429, outage, dead credential). Consumed by callers that compose
# `.with_fallbacks(..., exceptions_to_handle=FALLBACK_EXCEPTIONS)`.
FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (Exception,)


def _build_chat_model(
    provider: str, model: str, settings: Settings
) -> BaseChatModel:
    """Build one chat model from an explicit (provider, model) pair.

    This is the single place that knows how to turn a provider name into a
    concrete `BaseChatModel`. Both the primary model and every fallback go
    through here, so they share the exact same robustness knobs and branching.
    """
    provider = provider.lower()

    # Robustness knobs forwarded to every provider so a transient 429 / network
    # blip is retried with backoff instead of crashing the turn. These are the
    # widely-supported standard kwargs; `timeout` is omitted when unset so we
    # never pass None to a provider that dislikes it. NOTE: exact param support
    # varies slightly by provider — this is the pragmatic agnostic default.
    robustness: dict[str, object] = {"max_retries": settings.llm_max_retries}
    if settings.llm_timeout is not None:
        robustness["timeout"] = settings.llm_timeout

    # Case 1 — first-class provider: delegate to init_chat_model. Credentials are
    # auto-discovered from standard env vars (MISTRAL_API_KEY, GROQ_API_KEY, ...).
    if provider in _PROVIDER_ALIASES:
        return init_chat_model(
            model=model,
            model_provider=_PROVIDER_ALIASES[provider],
            temperature=settings.llm_temperature,
            **robustness,
        )

    # Case 2 — any OpenAI-compatible endpoint (self-hosted vLLM/Ollama, a third
    # party, or Foundry exposed as OpenAI). Just point base_url at it.
    # `stream_usage=True`: OpenAI omits token usage on STREAMED responses unless we
    # opt in (it then emits a final usage-only chunk). Without it, usage_metadata —
    # and with it `input_token_details.cache_read`, our prompt-cache hit rate — is
    # invisible on every streamed turn (the whole app streams). Safe here: this
    # branch is always OpenAI-wire. Lets the latency harness verify caching, and
    # cost/usage show up in LangSmith. See docs/prompt-caching.md.
    if provider == "openai_compatible":
        return init_chat_model(
            model=model,
            model_provider="openai",
            temperature=settings.llm_temperature,
            base_url=settings.llm_inference_endpoint,
            api_key=settings.llm_inference_api_key,
            stream_usage=True,
            **robustness,
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
        f"Unknown provider={provider!r}. "
        f"Expected one of: {', '.join(sorted(_PROVIDER_ALIASES))}, "
        f"openai_compatible, custom."
    )


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Build the configured PRIMARY chat model as an abstract `BaseChatModel`.

    Args:
        settings: Optional settings override (handy for tests). Defaults to the
            process-wide cached settings.

    Returns:
        A ready-to-use chat model exposing the standard LangChain interface
        (`.invoke()`, `.stream()`, tool binding, ...).
    """
    settings = settings or get_settings()
    return _build_chat_model(settings.llm_provider, settings.llm_model, settings)


def get_fast_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Build the FAST-role chat model (latency cascade), or the primary if unset.

    The router and the support node's tool-decision pass are cheap, easy LLM calls
    that sit on the critical path BEFORE the first streamed token. Running them on
    a small fast model (a `.env` change: `LLM_FAST_MODEL`, e.g. gpt-5.6-luna)
    shrinks the pre-roll silence (TTFT) while the strong model still writes the
    final, streamed answer. See `docs/latence.md`.

    Agnostic and safe by default: with no fast model configured we return the
    primary model, so the graph behaves exactly as before (no cascade). The fast
    provider defaults to the primary provider, so a fast deployment on the SAME
    endpoint (same base_url / api_key) needs only `LLM_FAST_MODEL` to be set.
    """
    settings = settings or get_settings()
    if not settings.llm_fast_model:
        return get_chat_model(settings)
    provider = settings.llm_fast_provider or settings.llm_provider
    return _build_chat_model(provider, settings.llm_fast_model, settings)


def get_chat_model_fallbacks(settings: Settings | None = None) -> list[BaseChatModel]:
    """Build the configured fallback chat model(s), or `[]` if none is set.

    Callers attach these to a (possibly tool-bound) runnable with
    `.with_fallbacks(...)` so a fully-down primary provider does not crash the
    turn. Returning a list keeps room for a future fallback CHAIN without
    changing the call sites.
    """
    settings = settings or get_settings()
    if not settings.llm_fallback_provider or not settings.llm_fallback_model:
        return []
    return [
        _build_chat_model(
            settings.llm_fallback_provider, settings.llm_fallback_model, settings
        )
    ]
