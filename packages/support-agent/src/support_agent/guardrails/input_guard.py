"""The input guard: one object that runs every entry-side check.

`InputGuard.check(text)` runs the three checks in cost order and returns a single
`GuardDecision` the graph node acts on:

    1. validation  -> length cap (cost / DoS) and empty input
    2. injection   -> known prompt-injection phrasings
    3. PII masking -> rewrite sensitive spans BEFORE the LLM/tools/store see them

The object is pure and framework-agnostic (no LangGraph import), so it is trivially
unit-testable and the graph node is thin glue around it. User-facing block messages live
here, in the demo's language (French).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from support_agent.guardrails.injection import (
    InjectionDetector,
    RegexInjectionDetector,
)
from support_agent.guardrails.moderation import (
    ContentModerator,
    RegexContentModerator,
)
from support_agent.guardrails.pii import (
    DEFAULT_POLICY,
    PIIDetector,
    PIIStrategy,
    RegexPIIDetector,
    apply_pii_policy,
)

INJECTION_MESSAGE = (
    "Je suis l'assistant du service client et je peux seulement vous aider sur vos "
    "commandes, livraisons, retours, remboursements ou votre compte. Que puis-je "
    "faire pour vous ?"
)
TOO_LONG_MESSAGE = (
    "Votre message est un peu long pour que je puisse le traiter d'un coup. "
    "Pourriez-vous le résumer en quelques phrases ?"
)
PII_BLOCK_MESSAGE = (
    "Pour votre sécurité, merci de ne pas partager d'informations sensibles ici "
    "(numéro de carte complet, identifiants). Je peux vous aider sans ces données."
)
MODERATION_MESSAGE = (
    "Je ne peux pas répondre à ce message. Je reste à votre disposition pour "
    "toute question sur vos commandes, livraisons, retours ou remboursements."
)
SELF_HARM_MESSAGE = (
    "Je suis un assistant du service client et je ne suis pas en mesure de vous "
    "aider sur ce sujet, mais vous n'êtes pas seul·e : si vous traversez un moment "
    "difficile, parlez-en à un proche, à votre médecin, ou à un service d'écoute "
    "près de vous, qui saura vous accompagner."
)
MODERATION_MESSAGES: dict[str, str] = {"self_harm": SELF_HARM_MESSAGE}


@dataclass(frozen=True)
class GuardDecision:
    """What the guard decided about one input message."""

    blocked: bool
    reason: str | None = None
    user_message: str | None = None
    sanitized_text: str = ""
    pii_entities: list[str] = field(default_factory=list)


class InputGuard:
    """Runs validation + injection + PII checks over an incoming user message."""

    def __init__(
        self,
        *,
        pii_detector: PIIDetector,
        injection_detector: InjectionDetector,
        max_input_chars: int,
        pii_policy: dict[str, PIIStrategy] | None = None,
        content_moderator: ContentModerator | None = None,
    ) -> None:
        self._pii = pii_detector
        self._injection = injection_detector
        self._max_input_chars = max_input_chars
        self._pii_policy = pii_policy or DEFAULT_POLICY
        self._moderator = content_moderator

    def check(self, text: str) -> GuardDecision:
        if not text.strip():
            return GuardDecision(
                blocked=True, reason="empty_input", user_message=INJECTION_MESSAGE,
                sanitized_text=text,
            )
        if len(text) > self._max_input_chars:
            return GuardDecision(
                blocked=True, reason="input_too_long", user_message=TOO_LONG_MESSAGE,
                sanitized_text=text,
            )

        if self._injection.scan(text):
            return GuardDecision(
                blocked=True, reason="prompt_injection",
                user_message=INJECTION_MESSAGE, sanitized_text=text,
            )

        if self._moderator is not None:
            category = self._moderator.scan(text)
            if category is not None:
                return GuardDecision(
                    blocked=True,
                    reason=f"content_{category}",
                    user_message=MODERATION_MESSAGES.get(category, MODERATION_MESSAGE),
                    sanitized_text=text,
                )

        pii = apply_pii_policy(text, self._pii, self._pii_policy)
        if pii.blocked:
            return GuardDecision(
                blocked=True, reason="pii_block", user_message=PII_BLOCK_MESSAGE,
                sanitized_text=text, pii_entities=pii.entities,
            )
        return GuardDecision(
            blocked=False, sanitized_text=pii.text, pii_entities=pii.entities,
        )


def build_input_guard(
    max_input_chars: int,
    pii_detector: PIIDetector | None = None,
    injection_detector: InjectionDetector | None = None,
    content_moderator: ContentModerator | None = None,
) -> InputGuard:
    """Assemble the default input guard (baseline detectors behind the ports)."""
    return InputGuard(
        pii_detector=pii_detector or RegexPIIDetector(),
        injection_detector=injection_detector or RegexInjectionDetector(),
        max_input_chars=max_input_chars,
        content_moderator=content_moderator or RegexContentModerator(),
    )
