"""The input guard: one object that runs every entry-side check (Phase 12-A).

`InputGuard.check(text)` runs the three checks in cost order and returns a single
`GuardDecision` the graph node acts on:

    1. validation  -> length cap (cost / DoS) and empty input
    2. injection   -> known prompt-injection phrasings
    3. PII masking -> rewrite sensitive spans BEFORE the LLM/tools/store see them

The object is pure and framework-agnostic (no LangGraph import): it is trivially
unit-testable, and the graph node (`make_guard_input`) is just thin glue around it.
User-facing block messages live here, in the demo's language (French).
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

# On-topic redirect shown when we refuse an input. It deliberately does NOT reveal
# WHY (injection vs. policy): telling an attacker what tripped the filter only
# helps them tune the next attempt.
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
# Harmful content. UNLIKE the injection refusal, this one names the reason —
# there is no attacker to keep in the dark, and a customer who crossed a line
# deserves to know which one rather than being stonewalled.
MODERATION_MESSAGE = (
    "Je ne peux pas répondre à ce message. Je reste à votre disposition pour "
    "toute question sur vos commandes, livraisons, retours ou remboursements."
)
# Self-harm gets its OWN message, and this is the reason the moderator returns a
# category instead of a boolean. Answering a person in distress with the generic
# refusal above would be the wrong thing to say to them. No number is hard-coded:
# it would be wrong outside France, and a wrong emergency number is worse than
# none — the concrete line belongs in configuration, per deployment.
SELF_HARM_MESSAGE = (
    "Je suis un assistant du service client et je ne suis pas en mesure de vous "
    "aider sur ce sujet, mais vous n'êtes pas seul·e : si vous traversez un moment "
    "difficile, parlez-en à un proche, à votre médecin, ou à un service d'écoute "
    "près de vous, qui saura vous accompagner."
)
# Category -> what the customer reads. A category with no entry falls back to
# MODERATION_MESSAGE, so adding a rule to the moderator can never crash a turn.
MODERATION_MESSAGES: dict[str, str] = {"self_harm": SELF_HARM_MESSAGE}


@dataclass(frozen=True)
class GuardDecision:
    """What the guard decided about one input message."""

    blocked: bool
    # Machine-readable reason, for logs and LangSmith traces (never shown raw).
    reason: str | None = None
    # What to reply to the customer if blocked.
    user_message: str | None = None
    # PII-masked version of the input (identical to the original if unchanged).
    sanitized_text: str = ""
    # Entity types that were masked (for logging / tracing).
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
        # Optional so an existing caller that built an InputGuard by hand keeps
        # working with moderation simply absent, rather than silently enabled.
        self._moderator = content_moderator

    def check(self, text: str) -> GuardDecision:
        # 1. Deterministic validation (cheapest, no detection needed).
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

        # 2. Prompt-injection heuristic.
        if self._injection.scan(text):
            return GuardDecision(
                blocked=True, reason="prompt_injection",
                user_message=INJECTION_MESSAGE, sanitized_text=text,
            )

        # 3. Harmful content. AFTER injection (an injection attempt dressed up as
        # an insult should still read as injection in the logs) and BEFORE PII
        # masking, because there is no point masking a message we are refusing.
        if self._moderator is not None:
            category = self._moderator.scan(text)
            if category is not None:
                return GuardDecision(
                    blocked=True,
                    # The category IS the log line — this is why the port returns
                    # a category rather than a boolean.
                    reason=f"content_{category}",
                    user_message=MODERATION_MESSAGES.get(category, MODERATION_MESSAGE),
                    sanitized_text=text,
                )

        # 4. PII masking — does NOT block the turn (customer stays served), unless
        # a 'block'-strategy entity is present in the policy.
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
