"""Agnostic LLM layer: provider selection lives here and nowhere else."""

from support_agent.llm.factory import get_chat_model

__all__ = ["get_chat_model"]
