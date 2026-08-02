"""The 8 quality cases, run through the eval harness.

`data/eval/quality_cases.jsonl` asks eight ordinary support questions and states the
FACT each answer must carry — a price, a delay, a return window, a carrier. They go
through `make_target()` and `ALL_EVALUATORS`, so a case that regresses fails `make
check` exactly like a case from `EVAL_CASES`.

Two things this module arranges that `test_eval.py` does not. The corpus speaks the SQL
shop's id convention, so it seeds a throwaway shop under `tmp_path` and switches
`SUPPORT_BACKEND` for its own duration rather than flipping the default — which also
exercises the eval harness against the other adapter. And `q-stock` is not in the run:
the business port has no availability capability, so the case is named in
`UNSUPPORTED_QUALITY_CASES` and pinned by a structural test rather than quietly dropped.

INTEGRATION tests: they drive the real graph against a real provider, so they are
skipped when no credentials are configured.
"""

from __future__ import annotations

import os

import pytest

from support_agent.actions import backend as backend_module
from support_agent.actions.backend import InMemorySupportBackend, SupportBackend
from support_agent.actions.sql.backend import SqlSupportBackend
from support_agent.actions.sql.seed import main as seed_main
from support_agent.config import get_settings
from support_agent.eval.corpus import (
    UNSUPPORTED_QUALITY_CASES,
    load_quality_cases,
)
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
    provider = get_settings().llm_provider.lower()
    env_var = _PROVIDER_KEY_ENV.get(provider, "LLM_INFERENCE_API_KEY")
    return bool(os.environ.get(env_var))


_CASES = load_quality_cases()


# --- The gap, pinned structurally (offline, always runs) ---------------------


def test_stock_availability_is_not_in_the_business_port() -> None:
    """Why `q-stock` is excluded, asserted instead of asserted-in-a-comment.

    The port is order lookup, ticket creation and ticket listing. Nothing reads stock,
    and the corpus's expected token `disponible` is a word a refusal contains too, so
    scoring it as a substring would pass on the opposite of the intended answer.

    Adding an availability method later makes this test fail, which is the point: the
    case must come back into the run rather than stay forgotten.
    """
    assert UNSUPPORTED_QUALITY_CASES == {"q-stock"}
    port_methods = {name for name in SupportBackend.__protocol_attrs__}
    assert port_methods == {"get_order_status", "create_ticket", "list_tickets"}
    for adapter in (InMemorySupportBackend, SqlSupportBackend):
        assert not any("stock" in name or "availab" in name for name in dir(adapter))


def test_the_run_covers_every_supported_case() -> None:
    """7 of 8 in the run, and the 8th accounted for — no silent shrinkage."""
    assert len(_CASES) == 7
    assert len(load_quality_cases(include_unsupported=True)) == 8


# --- The corpus, against the real agent -------------------------------------

_needs_llm = pytest.mark.skipif(
    not _has_llm_credentials(),
    reason="No LLM provider credentials configured — skipping integration eval.",
)


@pytest.fixture(scope="module")
def shop_backend(tmp_path_factory):
    """Point the process at a freshly seeded throwaway shop, for this module only.

    Module-scoped because seeding and graph construction are the expensive part;
    the teardown restores the process-wide caches so the rest of the suite sees
    the configured backend again.
    """
    db = tmp_path_factory.mktemp("shop") / "shop.db"
    seed_main(["--db-path", str(db)])

    previous = {
        "SUPPORT_BACKEND": os.environ.get("SUPPORT_BACKEND"),
        "SHOP_DB_PATH": os.environ.get("SHOP_DB_PATH"),
    }
    os.environ["SUPPORT_BACKEND"] = "sqlite"
    os.environ["SHOP_DB_PATH"] = str(db)
    get_settings.cache_clear()
    backend_module.reset_backend_cache()

    yield backend_module.get_backend()

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()
    backend_module.reset_backend_cache()


@pytest.fixture(scope="module")
def target(shop_backend):
    """The REAL graph, built once, in evaluation mode, over the seeded shop.

    `learn_from_turns=False` for the same reason as `test_eval.py`: a benchmark
    must not teach the thing it grades.
    """
    assert isinstance(shop_backend, SqlSupportBackend), (
        "the quality corpus needs the SQL double — its ids are the shop's"
    )
    return make_target(build_support_graph(learn_from_turns=False))


@_needs_llm
@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_quality_case(target, case: dict) -> None:
    """Every applicable evaluator must score the agent's answer as passing."""
    outputs = target(case["inputs"])
    reference = case["outputs"]

    for evaluator in ALL_EVALUATORS:
        feedback = evaluator(case["inputs"], outputs, reference)
        if feedback is None:
            continue
        assert feedback["score"], (
            f"[{case['id']}] {feedback['key']} failed — "
            f"expected={reference.get('expect_substring')!r} "
            f"route={outputs['route']!r} answer={outputs['answer']!r:.200} "
            f"tool_output={outputs['tool_output']!r:.200}"
        )
