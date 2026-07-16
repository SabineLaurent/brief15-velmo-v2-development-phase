"""PII detection & masking (Phase 12-A, input guardrails).

Agnostic by design: `PIIDetector` is a **port** (a `Protocol`). The baseline
adapter here is pure-regex — zero dependency, zero extra LLM call — and can be
swapped for Presidio or an LLM-based detector without touching the graph, exactly
like `SupportBackend` (`actions/`) and the LLM factory.

Deterministic-first: regex is cheap and predictable. It WILL miss exotic formats
and may over-match — that is an accepted trade-off for a first line of defense.
A smarter detector goes behind the same port when needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

# What to do with a detected entity:
#   redact -> replace with a "[REDACTED_<ENTITY>]" placeholder (safest)
#   mask   -> keep the shape but hide the middle (e.g. "j***@***")
#   block  -> refuse the whole turn (for things that must never be sent at all)
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


# Baseline patterns. Deliberately conservative and ORDER MATTERS: the most
# specific/structured entities come first so that, on overlap, they win over the
# greedy `phone` pattern (see `apply_pii_policy`). We would rather miss an exotic
# format (add a better detector behind the port) than mangle an order id.
_DEFAULT_PATTERNS: dict[str, re.Pattern[str]] = {
    # 12–20 digits grouped in 4s (covers most card numbers). No Luhn check here —
    # that is a refinement for a real detector behind the port.
    "credit_card": re.compile(r"\b\d{4}(?:[ -]?\d{4}){2,4}\b"),
    # IBAN: 2-letter country code + 2 check digits + grouped alphanumerics.
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{2,4}){3,8}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # 9–15 digits with optional separators and an optional leading '+'. The
    # lookarounds keep it from biting into alphanumerics like 'CMD-1001'.
    "phone": re.compile(r"(?<!\w)\+?\d(?:[ .\-]?\d){8,14}(?!\w)"),
}

# Default policy: mask everything by placeholder. Card numbers are the most
# sensitive, but even an email is worth keeping out of the model and the store.
DEFAULT_POLICY: dict[str, PIIStrategy] = {
    "credit_card": "redact",
    "iban": "redact",
    "email": "redact",
    "phone": "redact",
}


@dataclass(frozen=True)
class PIIResult:
    """Outcome of applying a PII policy to a text."""

    text: str  # sanitized text (identical to the input if nothing was changed)
    entities: list[str]  # distinct entity types found (for logging / tracing)
    blocked: bool  # True if a 'block'-strategy entity was present


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

    # Priority by policy order; sort by position then priority, then greedily keep
    # non-overlapping spans (the earlier/higher-priority match wins a conflict).
    priority = {entity: i for i, entity in enumerate(policy)}
    matches.sort(key=lambda m: (m.start, priority.get(m.entity, len(policy))))
    accepted: list[PIIMatch] = []
    last_end = -1
    for m in matches:
        if m.start < last_end:  # overlaps an already-accepted span
            continue
        accepted.append(m)
        last_end = m.end

    # A single 'block'-strategy entity vetoes the whole turn.
    if any(policy.get(m.entity) == "block" for m in accepted):
        found = sorted({m.entity for m in accepted})
        return PIIResult(text, found, True)

    # Rebuild the sanitized string span by span.
    out: list[str] = []
    cursor = 0
    for m in accepted:
        out.append(text[cursor : m.start])
        strategy = policy.get(m.entity, "redact")
        if strategy == "mask":
            out.append(_mask(m.value))
        else:  # redact (default)
            out.append(f"[REDACTED_{m.entity.upper()}]")
        cursor = m.end
    out.append(text[cursor:])
    return PIIResult("".join(out), sorted({m.entity for m in accepted}), False)
