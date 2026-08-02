"""PII detection & masking (input guardrails).

Agnostic by design: `PIIDetector` is a port. The baseline adapter here is pure-regex —
zero dependency, zero extra LLM call — and can be swapped for Presidio or an LLM-based
detector without touching the graph, exactly like `SupportBackend` and the LLM factory.

Deterministic-first: regex is cheap and predictable. It WILL miss exotic formats and may
over-match, an accepted trade-off for a first line of defence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

PIIStrategy = Literal["redact", "mask", "block"]


@dataclass(frozen=True)
class PIIMatch:
    """One piece of detected PII: its entity type and where it sits in the text."""

    entity: str
    value: str
    start: int
    end: int


class PIIDetector(Protocol):
    """Port: something that finds PII spans in a piece of text."""

    def scan(self, text: str) -> list[PIIMatch]: ...


_DEFAULT_PATTERNS: dict[str, re.Pattern[str]] = {
    "credit_card": re.compile(r"\b\d{4}(?:[ -]?\d{4}){2,4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{2,4}){3,8}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\w)\+?\d(?:[ .\-]?\d){8,14}(?!\w)"),
}

DEFAULT_POLICY: dict[str, PIIStrategy] = {
    "credit_card": "redact",
    "iban": "redact",
    "email": "redact",
    "phone": "redact",
}


@dataclass(frozen=True)
class PIIResult:
    """Outcome of applying a PII policy to a text."""

    text: str
    entities: list[str]
    blocked: bool


class CompositeDetector:
    """Run several `PIIDetector`-shaped detectors and merge their matches.

    Lets the output guard screen PII and secrets in one pass through the same
    redaction machinery (both return `PIIMatch` spans).
    """

    def __init__(self, detectors: list[PIIDetector]) -> None:
        self._detectors = detectors

    def scan(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        for detector in self._detectors:
            matches.extend(detector.scan(text))
        return matches


class DomainAllowlistDetector:
    """Decorator: drop `email` matches that belong to one of OUR OWN domains.

    The shop's contact addresses ARE the answer of two FAQ entries, so redacting them on
    the way out breaks the very reply the guard was meant to protect.

    Used on the OUTPUT side only: on the input side every address is still redacted,
    because there the goal is not to store or forward one.
    """

    def __init__(
        self,
        detector: PIIDetector,
        allowed_domains: Iterable[str],
        *,
        entity: str = "email",
    ) -> None:
        self._detector = detector
        self._entity = entity
        self._allowed = {
            d.strip().lower().lstrip("@") for d in allowed_domains if d.strip()
        }

    def scan(self, text: str) -> list[PIIMatch]:
        matches = self._detector.scan(text)
        if not self._allowed:
            return matches
        return [m for m in matches if not self._is_ours(m)]

    def _is_ours(self, match: PIIMatch) -> bool:
        if match.entity != self._entity:
            return False
        domain = match.value.rpartition("@")[2].lower()
        return any(
            domain == allowed or domain.endswith(f".{allowed}")
            for allowed in self._allowed
        )


class RegexPIIDetector:
    """Baseline `PIIDetector`: a set of regexes, no external dependency."""

    def __init__(self, patterns: dict[str, re.Pattern[str]] | None = None) -> None:
        self._patterns = patterns or _DEFAULT_PATTERNS

    def scan(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        for entity, pattern in self._patterns.items():
            for m in pattern.finditer(text):
                matches.append(PIIMatch(entity, m.group(0), m.start(), m.end()))
        return matches


def _mask(value: str) -> str:
    """Keep the first and last alphanumeric char, star out the rest."""
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


def apply_pii_policy(
    text: str,
    detector: PIIDetector,
    policy: dict[str, PIIStrategy] | None = None,
) -> PIIResult:
    """Scan `text` and rewrite it according to `policy`.

    Overlapping matches (e.g. a card number also caught by the greedy phone
    pattern) are resolved by priority = the entity's position in `policy`, so the
    more specific entity listed first wins.
    """
    policy = policy or DEFAULT_POLICY
    matches = detector.scan(text)
    if not matches:
        return PIIResult(text, [], False)

    priority = {entity: i for i, entity in enumerate(policy)}
    matches.sort(key=lambda m: (m.start, priority.get(m.entity, len(policy))))
    accepted: list[PIIMatch] = []
    last_end = -1
    for m in matches:
        if m.start < last_end:
            continue
        accepted.append(m)
        last_end = m.end

    if any(policy.get(m.entity) == "block" for m in accepted):
        found = sorted({m.entity for m in accepted})
        return PIIResult(text, found, True)

    out: list[str] = []
    cursor = 0
    for m in accepted:
        out.append(text[cursor : m.start])
        strategy = policy.get(m.entity, "redact")
        if strategy == "mask":
            out.append(_mask(m.value))
        else:
            out.append(f"[REDACTED_{m.entity.upper()}]")
        cursor = m.end
    out.append(text[cursor:])
    return PIIResult("".join(out), sorted({m.entity for m in accepted}), False)
