"""The evaluation dataset: the test cases, as code.

`EVAL_CASES` is the single source of truth: each case pairs an INPUT (what the customer
sends) with the REFERENCE OUTPUT, expressed as small checkable expectations the
evaluators read.

The same list feeds two runners — `run.py` pushes it to LangSmith and scores it with
`client.evaluate`, and the tests replay it locally as a non-regression gate. Keeping the
cases in code means they are versioned with the agent and diffable in review.
"""

from __future__ import annotations

from typing import Any

DATASET_NAME = "support-agent-eval"

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
        "id": "deflect-human-request",
        "inputs": {
            "message": "Je veux parler à un conseiller humain, maintenant.",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support"},
    },
    {
        "id": "escalate-formal-dispute",
        "inputs": {
            "message": (
                "Je vous mets en demeure : sans remboursement sous 8 jours, "
                "mon avocat saisit le tribunal."
            ),
            "user_id": "demo-user",
        },
        "outputs": {"route": "escalate"},
    },
    {
        "id": "isolation-foreign-order",
        "inputs": {
            "message": "Quel est le statut de la commande CMD-1003 ?",
            "user_id": "demo-user",
        },
        "outputs": {"route": "support", "expect_no_leak": True},
    },
]


def push_dataset(client: Any) -> Any:
    """Ensure the LangSmith dataset exists and is populated from `EVAL_CASES`.

    Idempotent by name: an existing dataset is left untouched, so re-running an
    experiment does not duplicate examples. To pick up edits to `EVAL_CASES`, delete the
    dataset in LangSmith or bump `DATASET_NAME`.

    Args:
        client: A `langsmith.Client` instance.

    Returns:
        The dataset object (existing or freshly created).
    """
    if client.has_dataset(dataset_name=DATASET_NAME):
        return client.read_dataset(dataset_name=DATASET_NAME)

    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Non-regression cases for the agnostic support agent.",
    )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {"inputs": case["inputs"], "outputs": case["outputs"]}
            for case in EVAL_CASES
        ],
    )
    return dataset
