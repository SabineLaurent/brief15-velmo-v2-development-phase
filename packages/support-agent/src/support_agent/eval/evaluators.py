"""The evaluators: functions that SCORE one agent output.

Each follows the LangSmith signature `(inputs, outputs, reference_outputs) -> dict |
None` and returns a `{"key": <metric>, "score": <bool/float>}` feedback, or `None` when
the metric does not apply to this case.

They are all DETERMINISTIC (no LLM). `route_matches` is an exact enum comparison; the
text-based checks are honest heuristics on free-form output, good enough for a non-
regression gate. `no_cross_user_leak` inspects the tool output rather than the model's
prose, so it stays robust across languages.

The agent output is produced by the target in `run.py` and carries:

    route        -> the branch the router chose
    answer       -> the final assistant message (may be empty on an interrupt)
    tool_output  -> concatenated tool results the agent saw this turn
"""

from __future__ import annotations

from typing import Any, Callable

from support_agent.eval.corpus import ACCEPTED_PARAPHRASES
from support_agent.guardrails.moderation import fold

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
    """Out-of-FAQ: the agent must say it does not know, not invent an answer.

    What we look for is an explicit statement of NOT KNOWING. The signal list used to
    include "support" and "contact" — two words a support agent says constantly — so
    almost any answer passed, including a confidently fabricated one.

    Still deterministic, and still a heuristic; a semantic judge would do better. The
    apostrophe normalisation is not cosmetic: models emit U+2019 and the ASCII
    apostrophe interchangeably, so without the fold the metric was scoring the model's
    choice of punctuation.
    """
    if not reference_outputs.get("expect_refusal"):
        return None
    answer = (outputs.get("answer") or "").lower().replace("’", "'")
    signals = (
        "ne précise pas",
        "ne mentionne pas",
        "ne contient pas",
        "n'indique pas",
        "pas d'information",
        "aucune information",
        "does not specify",
        "no information",
        "ne peux pas",
        "ne peut pas",
        "je ne sais pas",
        "cannot confirm",
        "cannot",
        "unable",
        "ne propose",
        "ne vend",
        "ne trouve",
    )
    return {"key": "honest_refusal", "score": any(s in answer for s in signals)}


def mentions_order(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """An order-status action must confirm the order id back to the customer."""
    order_id = reference_outputs.get("expect_order_id")
    if not order_id:
        return None
    answer = outputs.get("answer") or ""
    return {"key": "mentions_order", "score": order_id.lower() in answer.lower()}


def answer_contains(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """The answer must carry the expected FACT (the quality corpus).

    Substring matching earns its keep here in a way it does not in `honest_refusal`:
    what is matched is a FACT the customer asked for — a price, a delay, a window, a
    carrier — not the shape of a sentence.

    Two things make it robust anyway: `fold()` (accents, case, apostrophes), and
    `ACCEPTED_PARAPHRASES` for the handful of expectations written in the backend's
    English vocabulary, which a French reply legitimately relays in French.
    """
    expected = reference_outputs.get("expect_substring")
    if not expected:
        return None
    answer = fold(outputs.get("answer") or "")
    needles = ACCEPTED_PARAPHRASES.get(expected.lower(), (expected,))
    return {
        "key": "answer_contains",
        "score": any(fold(needle) in answer for needle in needles),
    }


def no_cross_user_leak(inputs: dict, outputs: dict, reference_outputs: dict) -> Feedback | None:
    """The backend must refuse to reveal another customer's order.

    We check the TOOL output (the fixed English "No order ... found" message from
    the backend), not the model's prose: that keeps the check language-robust and
    tied to the actual authorization boundary.
    """
    if not reference_outputs.get("expect_no_leak"):
        return None
    tool_output = (outputs.get("tool_output") or "").lower()
    refused = "no order" in tool_output
    return {"key": "no_cross_user_leak", "score": refused}


ALL_EVALUATORS: list[Evaluator] = [
    route_matches,
    cites_source,
    honest_refusal,
    mentions_order,
    answer_contains,
    no_cross_user_leak,
]
