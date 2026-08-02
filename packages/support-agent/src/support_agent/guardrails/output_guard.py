"""The output guard: last check before a reply reaches the customer.

Symmetric to `InputGuard`. `OutputGuard.check(text)` runs deterministic checks on the
agent's reply and returns an `OutputDecision`:

    1. system-prompt leak -> the model echoed our instructions: REPLACE the
       whole reply with a safe message.
    2. content moderation -> hate / violence / self-harm / sexual content in OUR
       OWN reply: REPLACE too. There is no span to cut out of a hateful
       sentence.
    3. PII + secret redaction -> defence in depth on the generated text.

Pure and framework-agnostic, like `InputGuard`. Off-domain refusal is deliberately not
here: it is a semantic judgement, not a deterministic one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from support_agent.guardrails.moderation import (
    ContentModerator,
    RegexContentModerator,
)
from support_agent.guardrails.pii import (
    CompositeDetector,
    DomainAllowlistDetector,
    PIIDetector,
    RegexPIIDetector,
    apply_pii_policy,
)
from support_agent.guardrails.secrets import RegexSecretDetector, SecretDetector

SAFE_OUTPUT_MESSAGE = (
    "Je ne peux pas partager cette information. Puis-je vous aider sur une "
    "commande, une livraison ou un retour ?"
)

_MIN_LEAK_LEN = 60


def _normalize(text: str) -> str:
    """Collapse whitespace and lowercase, for robust verbatim comparison."""
    return " ".join(text.split()).lower()


@dataclass(frozen=True)
class OutputDecision:
    """What the guard decided about one outgoing reply."""

    sanitized_text: str
    prompt_leak: bool = False
    replaced: bool = False
    findings: list[str] = field(default_factory=list)


class OutputGuard:
    """Runs prompt-leak detection + PII/secret redaction over an outgoing reply."""

    def __init__(
        self,
        *,
        detector: PIIDetector,
        protected_prompts: list[str],
        content_moderator: ContentModerator | None = None,
    ) -> None:
        self._detector = detector
        self._protected = [_normalize(p) for p in protected_prompts]
        self._moderator = content_moderator

    def _leaks_prompt(self, text: str) -> bool:
        haystack = _normalize(text)
        if len(haystack) < _MIN_LEAK_LEN:
            return False
        for prompt in self._protected:
            step = _MIN_LEAK_LEN // 2
            for i in range(0, max(1, len(prompt) - _MIN_LEAK_LEN + 1), step):
                if prompt[i : i + _MIN_LEAK_LEN] in haystack:
                    return True
        return False

    def check(self, text: str) -> OutputDecision:
        if self._leaks_prompt(text):
            return OutputDecision(
                sanitized_text=SAFE_OUTPUT_MESSAGE,
                prompt_leak=True,
                replaced=True,
                findings=["system_prompt"],
            )
        if self._moderator is not None:
            category = self._moderator.scan(text)
            if category is not None:
                return OutputDecision(
                    sanitized_text=SAFE_OUTPUT_MESSAGE,
                    replaced=True,
                    findings=[f"content_{category}"],
                )

        result = apply_pii_policy(text, self._detector)
        return OutputDecision(
            sanitized_text=result.text, findings=result.entities,
        )


def build_output_guard(
    protected_prompts: list[str],
    pii_detector: PIIDetector | None = None,
    secret_detector: SecretDetector | None = None,
    owned_email_domains: Iterable[str] = (),
    content_moderator: ContentModerator | None = None,
) -> OutputGuard:
    """Assemble the default output guard (PII + secret detectors behind ports).

    `owned_email_domains` are OUR public contact domains: their addresses are
    published by the knowledge base and must survive the outgoing redaction (a
    customer's address still does not). Asymmetric on purpose — the input guard
    gets no such allowlist.
    """
    detector = CompositeDetector(
        [
            DomainAllowlistDetector(
                pii_detector or RegexPIIDetector(), owned_email_domains
            ),
            secret_detector or RegexSecretDetector(),
        ]
    )
    return OutputGuard(
        detector=detector,
        protected_prompts=protected_prompts,
        content_moderator=content_moderator or RegexContentModerator(),
    )
