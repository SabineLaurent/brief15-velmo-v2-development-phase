"""Phase 10 robustness unit tests: provider fallback + graceful degradation.

Unlike `test_eval.py` (integration, needs real provider credentials), these are
pure UNIT tests: they inject fake chat models — one that always fails (a provider
that is "down"), one that always answers — so they run everywhere, with no `.env`
and no network. We assert two guarantees:

  1. Fallback — when the primary provider raises, the node transparently falls
     over to the secondary and the customer still gets a real answer.
  2. Graceful degradation — when EVERY provider is down, the node does not crash
     the turn; it returns the polite `GRACEFUL_ERROR_MESSAGE` instead.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult

from support_agent.config import Settings
from support_agent.graph.nodes import (
    GRACEFUL_ERROR_MESSAGE,
    _with_fallbacks,
    make_answer,
)
from support_agent.llm import get_chat_model_fallbacks


class _BoomChatModel(BaseChatModel):
    """A chat model that always fails — stands in for a provider that is down."""

    @property
    def _llm_type(self) -> str:
        return "boom"

    def _generate(self, messages: list, stop: Any = None, **kwargs: Any) -> ChatResult:
        raise RuntimeError("provider is down")


def test_no_fallback_configured_returns_empty_list() -> None:
    """No fallback provider set => the factory builds nothing (single-provider mode)."""
    settings = Settings(llm_fallback_provider=None, llm_fallback_model=None)
    assert get_chat_model_fallbacks(settings) == []


def test_with_fallbacks_uses_secondary_when_primary_fails() -> None:
    """A failing primary runnable transparently falls over to the secondary."""
    chain = _with_fallbacks(
        _BoomChatModel(), [FakeListChatModel(responses=["reply from fallback"])]
    )
    assert chain.invoke("hello").content == "reply from fallback"


def test_with_fallbacks_noop_without_fallbacks() -> None:
    """With no fallback configured, the primary runnable is returned unchanged."""
    primary = FakeListChatModel(responses=["primary reply"])
    assert _with_fallbacks(primary, []) is primary


def test_answer_node_falls_back_to_secondary_provider() -> None:
    """The answer node keeps working when the primary is down but a fallback is set."""
    answer = make_answer(
        _BoomChatModel(), [FakeListChatModel(responses=["bonjour du fallback"])]
    )
    result = answer({"messages": [HumanMessage("bonjour")]})
    assert result["messages"][0].content == "bonjour du fallback"


def test_answer_node_degrades_gracefully_when_all_providers_down() -> None:
    """With no working provider, the node returns the graceful reply, not a crash."""
    answer = make_answer(_BoomChatModel(), [])
    result = answer({"messages": [HumanMessage("bonjour")]})
    assert result["messages"][0].content == GRACEFUL_ERROR_MESSAGE
