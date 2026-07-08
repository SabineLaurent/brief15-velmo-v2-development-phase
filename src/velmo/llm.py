"""Clients LLM : Azure AI Inference (Kimi-K2.6) et repli local hors-ligne.

L'import du SDK Azure est différé pour que le harness démarre et que les tests
tournent sans dépendre du SDK ni d'un endpoint joignable.
"""

from __future__ import annotations

import os
from typing import Protocol

from .config import llm_backend, warn_backend_unavailable


class LLM(Protocol):
    """Interface minimale d'un client de complétion."""

    def invoke(self, system: str, context: str, message: str) -> str: ...


class EchoLLM:
    """Repli déterministe et hors-ligne : renvoie un accusé de réception.

    Permet au harness de conversation de démarrer sans identifiants Azure.
    """

    def invoke(self, system: str, context: str, message: str) -> str:
        return f"[velmo] J'ai bien reçu : {message}"


class AzureLLM:
    """Adapte le modèle de chat Azure AI Inference à l'interface `LLM`."""

    def __init__(self, model) -> None:
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"Mémoire:\n{context}"})
        messages.append({"role": "user", "content": message})
        return self._model.invoke(messages).content


def get_llm() -> LLM:
    """Si `VELMO_LLM=kimi`, construit le client Azure ; repli `EchoLLM` sinon."""
    if llm_backend() != "kimi":
        return EchoLLM()
    if not os.getenv("AZURE_AI_INFERENCE_ENDPOINT"):
        warn_backend_unavailable("LLM Azure", "AZURE_AI_INFERENCE_ENDPOINT absent")
        return EchoLLM()

    try:
        from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel
    except ImportError:
        warn_backend_unavailable("LLM Azure", "extra 'llm' non installé")
        return EchoLLM()

    model = AzureAIOpenAIApiChatModel(
        endpoint=os.environ["AZURE_AI_INFERENCE_ENDPOINT"],
        credential=os.environ["AZURE_AI_INFERENCE_API_KEY"],
        model=os.environ.get("AZURE_AI_INFERENCE_MODEL", "Kimi-K2.6"),
    )
    return AzureLLM(model)
