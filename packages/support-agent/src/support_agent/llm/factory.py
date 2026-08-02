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
from support_agent.llm._extras import provider_package_required

_PROVIDER_ALIASES: dict[str, str] = {
    "mistral": "mistralai",
    "groq": "groq",
    "google_genai": "google_genai",
    "openai": "openai",
    "azure_ai": "azure_ai",
}

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

    robustness: dict[str, object] = {"max_retries": settings.llm_max_retries}
    if settings.llm_timeout is not None:
        robustness["timeout"] = settings.llm_timeout

    if provider in _PROVIDER_ALIASES:
        with provider_package_required(provider):
            return init_chat_model(
                model=model,
                model_provider=_PROVIDER_ALIASES[provider],
                temperature=settings.llm_temperature,
                **robustness,
            )

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

    if provider == "custom":
        raise NotImplementedError(
            "Custom provider not implemented yet. Add an adapter in "
            "support_agent/llm/adapters/custom.py."
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

    The router sits on the critical path BEFORE the first streamed token, so running it
    on a small fast model (`LLM_FAST_MODEL`) shrinks the pre-roll silence while the
    strong model still writes the final, streamed answer.

    Agnostic and safe by default: with no fast model configured we return the primary
    model, so the graph behaves exactly as before. The fast provider defaults to the
    primary provider, so a fast deployment on the SAME endpoint needs only
    `LLM_FAST_MODEL` to be set.
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
