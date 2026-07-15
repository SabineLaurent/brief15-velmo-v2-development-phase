"""Evaluation & quality layer (Phase 9): score the agent automatically.

    dataset     -> the test cases, in code (single source of truth)
    evaluators  -> deterministic functions that score one agent output
    run         -> target(inputs) + `client.evaluate(...)` on LangSmith

The same cases and evaluators back both runners: `run.py` (LangSmith experiment,
comparable across providers) and `tests/test_eval.py` (a local pytest gate).
"""

# NOTE: we intentionally do NOT import from `run` here. `run` is the module you
# execute with `python -m support_agent.eval.run`; importing it in the package
# __init__ would re-import it before execution and trigger a RuntimeWarning.
from support_agent.eval.dataset import DATASET_NAME, EVAL_CASES, push_dataset
from support_agent.eval.evaluators import ALL_EVALUATORS

__all__ = [
    "DATASET_NAME",
    "EVAL_CASES",
    "push_dataset",
    "ALL_EVALUATORS",
]
