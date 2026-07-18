"""The evaluation dataset (Phase 9): the test cases, as code.

`EVAL_CASES` is the single source of truth: a list of behaviours the agent must
keep exhibiting. Each case pairs an INPUT (what the customer sends) with the
REFERENCE OUTPUT (what we expect) — expressed as small, checkable expectations
the evaluators read (see `evaluators.py`).

The same list feeds two runners:
    - `run.py`    -> pushes it to LangSmith and scores it with `client.evaluate`
    - tests/      -> replays it locally as a pytest non-regression gate

Keeping the cases in code (not only in the LangSmith UI) means they are
versioned with the agent and diffable in review.
"""

from __future__ import annotations

from typing import Any

# The LangSmith dataset name. Bumping it (or deleting the dataset) is how you
# force a resync after editing the cases below — `push_dataset` only creates the
# dataset when it does not already exist (see the note there).
DATASET_NAME = "support-agent-eval"

# Each case: `inputs` is sent to the agent; `outputs` is the reference the
# evaluators compare against. The expectation keys are deliberately small and
# explicit so each evaluator can decide whether it applies to a given case.
#
# Expectation keys used in `outputs`:
#   route            -> the branch the router MUST choose (exact enum match)
#   expect_citation  -> the answer must cite a FAQ source (e.g. "source: livraison.md")
#   expect_refusal   -> out-of-FAQ: the agent must decline honestly / offer a human
#   expect_order_id  -> the answer must mention this order id (action confirmed)
#   expect_no_leak   -> the backend must refuse to reveal another customer's order
EVAL_CASES: list[dict[str, Any]] = [
    {
        "id": "smalltalk-greeting",
        "inputs": {"message": "Bonjour !", "user_id": "demo-user"},
        "outputs": {"route": "answer"},
    },
    {
        "id": "faq-delivery-times",
        "inputs": {
            "message": "Quels sont les délais de livraison ?",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support", "expect_citation": True},
    },
    {
        "id": "out-of-faq-honest-refusal",
        "inputs": {
            "message": "Est-ce que vous vendez des billets d'avion ?",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support", "expect_refusal": True},
    },
    {
        "id": "action-order-status",
        "inputs": {
            "message": "Où en est ma commande CMD-1001 ?",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support", "expect_order_id": "CMD-1001"},
    },
    {
        "id": "escalate-human-request",
        "inputs": {
            "message": "Je veux parler à un conseiller humain, maintenant.",
            "user_id": "demo-user",
        },
        "outputs": {"route": "escalate"},
    },
    {
        "id": "isolation-foreign-order",
        "inputs": {
            # CMD-1003 belongs to "other-user": the agent must not reveal it.
            "message": "Quel est le statut de la commande CMD-1003 ?",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support", "expect_no_leak": True},
    },
]


def push_dataset(client: Any) -> Any:
    """Ensure the LangSmith dataset exists and is populated from `EVAL_CASES`.

    Idempotent by name: if the dataset already exists we leave it untouched (so
    re-running an experiment does not duplicate examples). To pick up edits to
    `EVAL_CASES`, delete the dataset in LangSmith or bump `DATASET_NAME`.

    Args:
        client: A `langsmith.Client` instance.

    Returns:
        The dataset object (existing or freshly created).
    """
    if client.has_dataset(dataset_name=DATASET_NAME):
        return client.read_dataset(dataset_name=DATASET_NAME)

    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Phase 9 non-regression cases for the agnostic support agent.",
    )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {"inputs": case["inputs"], "outputs": case["outputs"]}
            for case in EVAL_CASES
        ],
    )
    return dataset
