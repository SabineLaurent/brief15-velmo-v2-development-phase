"""Evaluation & quality layer: score the agent automatically.

    dataset     -> the test cases, in code (single source of truth)
    evaluators  -> deterministic functions that score one agent output
    run         -> target(inputs) + `client.evaluate(...)` on LangSmith

The same cases and evaluators back both runners: `run.py` (LangSmith experiment,
comparable across providers) and `tests/test_eval.py` (a local pytest gate).
"""

from support_agent.eval.dataset import DATASET_NAME, EVAL_CASES, push_dataset
from support_agent.eval.evaluators import ALL_EVALUATORS

__all__ = [
    "DATASET_NAME",
    "EVAL_CASES",
    "push_dataset",
    "ALL_EVALUATORS",
]
