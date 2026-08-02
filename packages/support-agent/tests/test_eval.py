"""Local non-regression gate: replay the eval cases through pytest.

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
def eval_graph():
    """The REAL graph, built once, in evaluation mode."""
    return build_support_graph(learn_from_turns=False)


@pytest.fixture(scope="module")
def target(eval_graph):
    """Expose the graph as the evaluation target."""
    return make_target(eval_graph)


def test_the_eval_graph_cannot_feed_episodic_memory(eval_graph) -> None:
    """A benchmark must not teach the thing it grades.

    Asserted on the compiled TOPOLOGY, not on a flag: the node being absent is
    the only proof that no code path can write a candidate, whatever the state
    or the branch taken. Recall stays wired — we grade the agent as deployed.
    """
    assert "close_turn" not in eval_graph.get_graph().nodes


@pytest.mark.parametrize("case", EVAL_CASES, ids=[c["id"] for c in EVAL_CASES])
def test_eval_case(target, case: dict) -> None:
    """Every applicable evaluator must score the agent's output as passing."""
    outputs = target(case["inputs"])
    reference = case["outputs"]

    for evaluator in ALL_EVALUATORS:
        feedback = evaluator(case["inputs"], outputs, reference)
        if feedback is None:
            continue
        assert feedback["score"], (
            f"[{case['id']}] {feedback['key']} failed — "
            f"route={outputs['route']!r} answer={outputs['answer']!r:.120} "
            f"tool_output={outputs['tool_output']!r:.120}"
        )
