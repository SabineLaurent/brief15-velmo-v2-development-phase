"""Security guardrails (Phase 12): keep malicious/sensitive input out and
unsafe/leaky output away from the customer.

Same agnostic spirit as the rest of the project: each detector is a **port**
(`Protocol`) with a baseline adapter, swappable without touching the graph.

Phase 12-A (this file's current scope) covers the INPUT side:

    InputGuard  ->  validation (length/empty) + prompt-injection + PII masking

wired into the graph as the `guard_input` node, before the router.
"""

from support_agent.guardrails.injection import (
    InjectionDetector,
    RegexInjectionDetector,
)
from support_agent.guardrails.input_guard import (
    GuardDecision,
    InputGuard,
    build_input_guard,
)
from support_agent.guardrails.pii import (
    DEFAULT_POLICY,
    PIIDetector,
    PIIMatch,
    PIIResult,
    PIIStrategy,
    RegexPIIDetector,
    apply_pii_policy,
)

__all__ = [
    "DEFAULT_POLICY",
    "GuardDecision",
    "InjectionDetector",
    "InputGuard",
    "PIIDetector",
    "PIIMatch",
    "PIIResult",
    "PIIStrategy",
    "RegexInjectionDetector",
    "RegexPIIDetector",
    "apply_pii_policy",
    "build_input_guard",
]
