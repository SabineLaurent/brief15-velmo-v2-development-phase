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

    What we look for is an explicit statement of NOT KNOWING or NOT HAVING the
    information. The signal list used to include "support" and "contact" — two
    words a support agent says constantly — so almost any answer passed, including
    a confidently fabricated one. That was finding Q1 of the 2026-07-19 audit, and
    it stayed invisible until a prompt change removed the boilerplate the lax
    signals were accidentally matching.

    Still deterministic, and still a heuristic: a semantic judge would do better.
    But it now fails a fabricated answer, which is the whole point of the metric.

    The apostrophe normalisation below is not cosmetic. Models emit the
    TYPOGRAPHIC apostrophe (U+2019) in French roughly as often as the ASCII one,
    and which one comes out varies between two runs of the same prompt. Without
    the fold, "la FAQ n’indique pas" scored 0 while "la FAQ n'indique pas" scored
    1 — the metric was measuring the model's choice of punctuation, and failing
    correct refusals at random.
    """
    if not reference_outputs.get("expect_refusal"):
        return None
    answer = (outputs.get("answer") or "").lower().replace("’", "'")
    signals = (
        # "I do not have / the FAQ does not say"
        "ne précise pas",
        "ne mentionne pas",
        "ne contient pas",
        "n'indique pas",
        "pas d'information",
        "aucune information",
        "does not specify",
        "no information",
        # "I cannot confirm / I do not know"
        "ne peux pas",
        "ne peut pas",
        "je ne sais pas",
        "cannot confirm",
        "cannot",
        "unable",
        # "we do not sell/offer that"
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
    """The answer must carry the expected FACT (the starter's quality corpus).

    Substring matching earns its keep here in a way it does not in
    `honest_refusal`: what is matched is a FACT the customer asked for — a price
    (`6,90`), a delay (`J+2`), a window (`14 jours`), a carrier (`Colissimo`) —
    not the shape of a sentence. A model can phrase the answer a hundred ways;
    all of them contain the number.

    Two properties make it robust anyway:
      - `fold()` (accents, case, apostrophes) — the same normalisation the
        moderation rules use, and the same class of bug `f404775` paid for;
      - `ACCEPTED_PARAPHRASES` for the handful of expectations written in the
        BACKEND's English vocabulary, which a French reply legitimately relays
        in French.
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
    answer_contains,
    no_cross_user_leak,
]
