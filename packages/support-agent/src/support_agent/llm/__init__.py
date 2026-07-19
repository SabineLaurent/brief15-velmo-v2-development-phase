"""Agnostic LLM layer: provider selection lives here and nowhere else."""

from support_agent.llm.factory import (
    FALLBACK_EXCEPTIONS,
    get_chat_model,
    get_chat_model_fallbacks,
    get_fast_chat_model,
)

__all__ = [
    "FALLBACK_EXCEPTIONS",
    "get_chat_model",
    "get_chat_model_fallbacks",
    "get_fast_chat_model",
]
