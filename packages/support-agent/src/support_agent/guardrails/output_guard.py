"""The output guard: last check before a reply reaches the customer (Phase 12-B).

Symmetric to `InputGuard`: `OutputGuard.check(text)` runs deterministic checks on
the agent's reply and returns an `OutputDecision` the graph node acts on:

    1. system-prompt leak -> the model echoed our instructions verbatim: REPLACE
       the whole reply with a safe message (never ship the leak).
    2. content moderation -> hate / violence / self-harm / sexual content in OUR
       OWN reply: REPLACE too. There is no span to cut out of a hateful sentence.
    3. PII + secret redaction -> defense in depth: mask anything sensitive that
       slipped into the generated text (customer PII, a leaked credential).

Pure and framework-agnostic (no LangGraph import), like `InputGuard`. Off-domain
refusal is intentionally NOT here: it is a semantic judgement (a classifier's job),
not a deterministic one — kept at the prompt level / future hardening.
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

# Shown instead of a reply that leaked our own instructions. Neutral, on-topic.
SAFE_OUTPUT_MESSAGE = (
    "Je ne peux pas partager cette information. Puis-je vous aider sur une "
    "commande, une livraison ou un retour ?"
)

# Minimum length of a verbatim overlap (normalized chars) to call it a prompt
# leak. Long enough that ordinary phrasing does not trip it.
_MIN_LEAK_LEN = 60


def _normalize(text: str) -> str:
    """Collapse whitespace and lowercase, for robust verbatim comparison."""
    return " ".join(text.split()).lower()


@dataclass(frozen=True)
class OutputDecision:
    """What the guard decided about one outgoing reply."""

    sanitized_text: str  # the reply to actually send (redacted, or replaced)
    prompt_leak: bool = False  # the reply echoed our system instructions
    replaced: bool = False  # the whole reply was swapped for the safe message
    findings: list[str] = field(default_factory=list)  # entities redacted (logs)


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
        # Pre-normalize the texts we never want to see echoed back.
        self._protected = [_normalize(p) for p in protected_prompts]
        # Optional, like on the input guard: a hand-built OutputGuard keeps its
        # previous behaviour rather than silently gaining a new check.
        self._moderator = content_moderator

    def _leaks_prompt(self, text: str) -> bool:
        haystack = _normalize(text)
        if len(haystack) < _MIN_LEAK_LEN:
            return False
        for prompt in self._protected:
            # Slide a window over the protected text; a verbatim chunk in the
            # output means the model repeated our instructions.
            step = _MIN_LEAK_LEN // 2
            for i in range(0, max(1, len(prompt) - _MIN_LEAK_LEN + 1), step):
                if prompt[i : i + _MIN_LEAK_LEN] in haystack:
                    return True
        return False

    def check(self, text: str) -> OutputDecision:
        # 1. A leaked system prompt is never shipped — replace the whole reply.
        if self._leaks_prompt(text):
            return OutputDecision(
                sanitized_text=SAFE_OUTPUT_MESSAGE,
                prompt_leak=True,
                replaced=True,
                findings=["system_prompt"],
            )
        # 2. Harmful content in OUR OWN reply. Not redacted — REPLACED, like a
        # prompt leak: there is no offending span to cut out of a hateful
        # sentence, the sentence is the problem. This is the second half of the
        # brief's "en entrée et en sortie": the input guard stops the customer
        # from bringing it in, this stops us from producing it — whether the model
        # went off the rails on its own or was steered there by an injection the
        # input guard did not recognise.
        if self._moderator is not None:
            category = self._moderator.scan(text)
            if category is not None:
                return OutputDecision(
                    sanitized_text=SAFE_OUTPUT_MESSAGE,
                    replaced=True,
                    findings=[f"content_{category}"],
                )

        # 3. Redact any PII / secret that slipped into the generated text.
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
