"""Security guardrails: keep malicious input out, and leaky output away.

Same agnostic spirit as the rest of the project: each detector is a port (`Protocol`)
with a baseline adapter, swappable without touching the graph.

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
from support_agent.guardrails.output_guard import (
    OutputDecision,
    OutputGuard,
    build_output_guard,
)
from support_agent.guardrails.pii import (
    DEFAULT_POLICY,
    CompositeDetector,
    DomainAllowlistDetector,
    PIIDetector,
    PIIMatch,
    PIIResult,
    PIIStrategy,
    RegexPIIDetector,
    apply_pii_policy,
)
from support_agent.guardrails.secrets import (
    RegexSecretDetector,
    SecretDetector,
)
from support_agent.guardrails.tool_guard import (
    RateLimiter,
    ToolGuard,
    build_tool_guard,
)

__all__ = [
    "DEFAULT_POLICY",
    "CompositeDetector",
    "DomainAllowlistDetector",
    "GuardDecision",
    "InjectionDetector",
    "InputGuard",
    "OutputDecision",
    "OutputGuard",
    "PIIDetector",
    "PIIMatch",
    "PIIResult",
    "PIIStrategy",
    "RateLimiter",
    "RegexInjectionDetector",
    "RegexPIIDetector",
    "RegexSecretDetector",
    "SecretDetector",
    "ToolGuard",
    "apply_pii_policy",
    "build_input_guard",
    "build_output_guard",
    "build_tool_guard",
]
