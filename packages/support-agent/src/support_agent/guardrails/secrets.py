"""Secret detection (Phase 12-B, output guardrails).

A support agent should never echo a credential back to a customer — neither its
own (a leaked key in the prompt/tools) nor one the customer pasted. `SecretDetector`
is a **port** shaped exactly like `PIIDetector` (it returns `PIIMatch` spans), so
secrets flow through the very same redaction machinery (`apply_pii_policy`) via a
`CompositeDetector`.

Deterministic-first and high-precision by choice: we match a few distinctive,
structured credential formats rather than anything key-shaped, to avoid redacting
legitimate content. The list is NOT exhaustive — a real deployment plugs a richer
detector behind the port.
"""

from __future__ import annotations

import re
from typing import Protocol

from support_agent.guardrails.pii import PIIMatch

# High-signal credential shapes. Deliberately narrow to keep false positives low.
#
# A pattern may expose a `secret` capture group to redact only PART of the match:
# the credential-in-prose rule below has to keep the sentence ("Le mot de passe
# est …") and remove only the value, otherwise the reply becomes unreadable and
# the customer cannot tell what happened.
_DEFAULT_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"\b(?:sk|gsk|xai|pk)[-_][A-Za-z0-9]{16,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"),
    # A credential ANNOUNCED IN PROSE — "Le mot de passe du compte client est
    # Velmo2024!". The structured shapes above cannot catch this: a human-chosen
    # password has no distinctive form, so the signal is the SENTENCE, not the
    # value. Measured gap: this was the one output case of the 35-case guardrail
    # corpus that got through (card number and IBAN were already redacted).
    #
    # The trailing `[^\s.,;:]` keeps sentence punctuation out of the redacted
    # span, so "…est Velmo2024!." redacts `Velmo2024!` and leaves the full stop.
    "password": re.compile(
        r"(?:mot\s+de\s+passe|password|identifiant|code\s+d['’]acc[èe]s)"
        r"\b.{0,40}?"
        r"(?:est|:|=)\s+"
        r"(?P<secret>\S{2,}[^\s.,;:])",
        re.IGNORECASE,
    ),
}


class SecretDetector(Protocol):
    """Port: find credential-looking spans in a piece of text."""

    def scan(self, text: str) -> list[PIIMatch]: ...


class RegexSecretDetector:
    """Baseline `SecretDetector`: a set of distinctive credential regexes."""

    def __init__(self, patterns: dict[str, re.Pattern[str]] | None = None) -> None:
        self._patterns = patterns or _DEFAULT_SECRET_PATTERNS

    def scan(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        for entity, pattern in self._patterns.items():
            for m in pattern.finditer(text):
                # Narrow the span to the `secret` group when the pattern defines
                # one, so only the credential is redacted and not the sentence
                # that announced it.
                group = "secret" if "secret" in pattern.groupindex else 0
                matches.append(
                    PIIMatch(entity, m.group(group), m.start(group), m.end(group))
                )
        return matches
