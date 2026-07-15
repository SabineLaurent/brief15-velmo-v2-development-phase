"""Local non-regression gate (Phase 9): replay the eval cases through pytest.

Same cases and same evaluators as the LangSmith run (`support_agent.eval`), but
asserted locally so a broken behaviour fails `make test`. These are INTEGRATION
tests: they drive the real graph, which calls a real LLM provider — so they are
SKIPPED when no provider credentials are configured (a fresh clone without a
`.env` should not fail here). Temperature is 0.0 (see config), which keeps the
routing decisions stable enough for a gate.
"""

from __future__ import annotations

import os

import pytest

from support_agent.config import get_settings
from support_agent.eval.dataset import EVAL_CASES
from support_agent.eval.evaluators import ALL_EVALUATORS
from support_agent.eval.run import make_target
from support_agent.graph import build_support_graph

# Which env var holds the credential, per provider. `openai_compatible` and any
# unknown provider fall back to the generic endpoint key.
_PROVIDER_KEY_ENV = {
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_ai": "AZURE_AI_API_KEY",
}


def _has_llm_credentials() -> bool:
    """True if the configured provider has a usable credential in the env."""
    provider = get_settings().llm_provider.lower()
    env_var = _PROVIDER_KEY_ENV.get(provider, "LLM_INFERENCE_API_KEY")
    return bool(os.environ.get(env_var))


pytestmark = pytest.mark.skipif(
    not _has_llm_credentials(),
    reason="No LLM provider credentials configured — skipping integration eval.",
)


@pytest.fixture(scope="module")
def target():
    """Build the graph once for the whole module and expose the eval target."""
    return make_target(build_support_graph())


@pytest.mark.parametrize("case", EVAL_CASES, ids=[c["id"] for c in EVAL_CASES])
def test_eval_case(target, case: dict) -> None:
    """Every applicable evaluator must score the agent's output as passing."""
    outputs = target(case["inputs"])
    reference = case["outputs"]

    for evaluator in ALL_EVALUATORS:
        feedback = evaluator(case["inputs"], outputs, reference)
        if feedback is None:
            continue  # this metric does not apply to this case
        assert feedback["score"], (
            f"[{case['id']}] {feedback['key']} failed — "
            f"route={outputs['route']!r} answer={outputs['answer']!r:.120} "
            f"tool_output={outputs['tool_output']!r:.120}"
        )
