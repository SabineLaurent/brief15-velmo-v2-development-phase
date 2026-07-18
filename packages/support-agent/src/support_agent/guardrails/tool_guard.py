"""Tool-boundary guardrails (Phase 12-C: side effects & persistence).

The input/output guards protect the CONVERSATION boundary. This one protects the
TOOL boundary — where the agent writes to the business backend and to durable
long-term memory. Tool arguments are LLM-generated from what the customer says,
so they can carry PII or be abused. `ToolGuard` gives the write tools three cheap,
deterministic protections:

    validate_field  -> cap field length (abuse / cost on the backend)
    sanitize        -> mask PII BEFORE it is persisted (never store a raw PAN)
    allow_action    -> per-key rate limit on side-effecting actions (anti-abuse)

Agnostic, like the rest: it reuses the `PIIDetector` port. The rate limiter is
in-process (fine for a single-process demo); a real deployment swaps it for a
shared/durable limiter (e.g. Redis) — the tool code would not change.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from support_agent.guardrails.pii import (
    PIIDetector,
    RegexPIIDetector,
    apply_pii_policy,
)


@dataclass
class RateLimiter:
    """A simple in-process sliding-window rate limiter, keyed by an arbitrary id.

    NOT shared across processes — a real deployment uses a durable store. Kept
    tiny on purpose: `allow(key)` records a hit and returns whether it is under
    the limit for the trailing `window_seconds`.
    """

    max_calls: int
    window_seconds: float
    _hits: dict[str, deque[float]] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        # Drop timestamps that fell out of the trailing window.
        cutoff = now - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True


@dataclass
class ToolGuard:
    """Guardrails the write tools apply to their (LLM-generated) arguments."""

    pii_detector: PIIDetector
    max_field_chars: int
    rate_limiter: RateLimiter

    def validate_field(self, text: str, *, field_name: str) -> str | None:
        """Return an error message if the field is invalid, else None."""
        if len(text) > self.max_field_chars:
            return (
                f"The '{field_name}' is too long "
                f"(max {self.max_field_chars} characters). Please shorten it."
            )
        return None

    def sanitize(self, text: str) -> str:
        """Mask any PII in text before it gets persisted."""
        return apply_pii_policy(text, self.pii_detector).text

    def allow_action(self, key: str) -> bool:
        """Whether a side-effecting action is allowed for `key` right now."""
        return self.rate_limiter.allow(key)


def build_tool_guard(
    max_field_chars: int,
    action_rate_limit: int,
    action_rate_window_seconds: float,
    pii_detector: PIIDetector | None = None,
) -> ToolGuard:
    """Assemble the default tool guard (baseline PII detector + rate limiter)."""
    return ToolGuard(
        pii_detector=pii_detector or RegexPIIDetector(),
        max_field_chars=max_field_chars,
        rate_limiter=RateLimiter(action_rate_limit, action_rate_window_seconds),
    )
