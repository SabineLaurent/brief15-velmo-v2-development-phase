"""The evaluators (Phase 9): functions that SCORE one agent output.

Each evaluator follows the LangSmith signature
`(inputs, outputs, reference_outputs) -> dict | None` and returns a
`{"key": <metric name>, "score": <bool/float>}` feedback — or `None` when the
metric does not apply to this case (LangSmith then records nothing, and the
pytest runner simply skips it).

Design choice: these are all **deterministic** (no LLM). The strongest signal,
`route_matches`, is an exact enum comparison. The text-based checks
(`cites_source`, `honest_refusal`) are honest heuristics on free-form output —
good enough for a non-regression gate, and the reason a semantic LLM-as-judge is
a natural later addition. `no_cross_user_leak` deliberately inspects the tool
output (a fixed English backend message), not the model's prose, so it stays
robust across languages.

The agent output (`outputs`) is produced by the target in `run.py` and carries:
    route        -> the branch the router chose
    answer        -> the final assistant message (may be empty on an interrupt)
    tool_output  -> concatenated tool results the agent saw this turn
"""

from __future__ import annotations

from typing import Any, Callable

Feedback = dict[str, Any]
Evaluator = Callable[[dict, dict, dict], Feedback | None]


def route_matches(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """Exact match on the router's branch — the core non-regression signal."""
    expected = reference_outputs.get("route")
    if expected is None:
        return None
    return {"key": "route_matches", "score": outputs.get("route") == expected}


def cites_source(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """A FAQ answer must cite its source (the prompt asks for 'source: <file>.md')."""
    if not reference_outputs.get("expect_citation"):
        return None
    answer = (outputs.get("answer") or "").lower()
    cited = "source" in answer and ".md" in answer
    return {"key": "cites_source", "score": cited}


def honest_refusal(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """Out-of-FAQ: the agent should decline honestly and/or point to a human.

    Heuristic: we look for a hand-off / inability signal rather than a confident
    fabricated answer. Kept lenient on purpose — a semantic judge would do better.
    """
    if not reference_outputs.get("expect_refusal"):
        return None
    answer = (outputs.get("answer") or "").lower()
    signals = (
        "conseiller",
        "humain",
        "human",
        "support",
        "contact",
        "désolé",
        "sorry",
        "ne propose",
        "ne vend",
        "ne trouve",
        "pas d'information",
        "unable",
        "cannot",
    )
    return {"key": "honest_refusal", "score": any(s in answer for s in signals)}


def mentions_order(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """An order-status action must confirm the order id back to the customer."""
    order_id = reference_outputs.get("expect_order_id")
    if not order_id:
        return None
    answer = outputs.get("answer") or ""
    return {"key": "mentions_order", "score": order_id.lower() in answer.lower()}


def no_cross_user_leak(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """The backend must refuse to reveal another customer's order.

    We check the TOOL output (the fixed English "No order ... found" message from
    the backend), not the model's prose: that keeps the check language-robust and
    tied to the actual authorization boundary.
    """
    if not reference_outputs.get("expect_no_leak"):
        return None
    tool_output = (outputs.get("tool_output") or "").lower()
    # The refusal message is emitted; the confidential status word must not be.
    refused = "no order" in tool_output
    return {"key": "no_cross_user_leak", "score": refused}


# The full suite, in the order they read best in a report. Both runners import
# this so LangSmith and pytest score exactly the same thing.
ALL_EVALUATORS: list[Evaluator] = [
    route_matches,
    cites_source,
    honest_refusal,
    mentions_order,
    no_cross_user_leak,
]
