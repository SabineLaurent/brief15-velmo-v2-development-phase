"""A message's text is `.text`, never `.content`.

`BaseMessage.content` is provider-native: a `str` for most providers, a LIST OF CONTENT
BLOCKS for others. `BaseMessage.text` is LangChain's normalisation of that difference,
and it is what lets this project read model output all over the place while claiming to
be provider-agnostic.

Reading `.content` broke in two ways, the second being the nastier:

    AttributeError   `.lower()` / `fold()` on a list — the eval gate went down
    SILENT GARBAGE   `str(.content)` stringified the list, so the guards scanned
                     a Python repr instead of the sentence

A test that only checked "it does not crash" would have passed on the broken version, so
these check the TEXT that comes out.

Offline: content blocks are constructed by hand, which is exactly what a blocks-
returning provider hands us.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from support_agent.api import _reply_from_result
from support_agent.graph.nodes import make_guard_input, make_guard_output
from support_agent.guardrails.input_guard import build_input_guard
from support_agent.guardrails.output_guard import SAFE_OUTPUT_MESSAGE, build_output_guard


def blocks(text: str) -> list[dict]:
    """Content as a blocks-returning provider sends it.

    Two blocks on purpose: a single-block list would also be satisfied by code
    that just grabs `content[0]["text"]`, and `.text` concatenates.
    """
    head, _, tail = text.partition(" ")
    return [{"type": "text", "text": head + " "}, {"type": "text", "text": tail}]


# --- The structural invariant, so this cannot silently come back -------------


_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "support_agent"


def test_no_module_reads_dot_content() -> None:
    """The invariant, executable: nothing in the package reads `.content`.

    A comment saying "use .text" is advice; this is a gate. The same reasonable-looking
    mistake was made in ten places over several phases with nothing watching.

    Read through the AST, not with a regex over lines: the first version matched its own
    explanatory comments. A line-based check has to choose between false positives and
    re-implementing a parser badly, whereas `ast` sees attribute ACCESS only, so
    `content=` keywords and `.content_blocks` are correctly invisible to it.

    A future feature that genuinely needs the provider-native shape should use
    `.content_blocks`, or add an explicit exemption here with its reason.
    """
    offenders: list[str] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "content":
                offenders.append(f"{path.relative_to(_SOURCE_ROOT)}:{node.lineno}")
    assert not offenders, (
        "read `.text`, not `.content` (provider-native, may be a list of blocks):\n  "
        + "\n  ".join(offenders)
    )


def test_langchain_still_normalises_blocks_for_us() -> None:
    """Pin the LangChain behaviour the whole fix rests on.

    Not paranoia about a stable API: this is the one assumption that, if it
    changed, would re-open Q2 everywhere at once and produce no error anywhere.
    A three-line test is cheaper than that.
    """
    assert AIMessage(content="plain").text == "plain"
    assert AIMessage(content=blocks("Bonjour Sabine")).text == "Bonjour Sabine"
    assert AIMessage(content=[]).text == ""
    mixed = AIMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "ok"}])
    assert mixed.text == "ok"


# --- The seam: the only thing a front sees ----------------------------------


def test_the_seam_delivers_a_blocks_reply_instead_of_the_error_message() -> None:
    """The regression this fix removes, stated as the customer experiences it.

    The old guard was `isinstance(last.content, str)`, which a blocks reply
    fails. It did not raise — it fell through to the "should not happen" branch
    and served GRACEFUL_ERROR_MESSAGE while a perfectly good answer sat in the
    state. An outage, on a provider swap, with nothing in the logs but one
    `logger.error`.
    """
    reply = _reply_from_result(
        {"messages": [AIMessage(content=blocks("Votre commande est expédiée."))]}
    )
    assert reply == "Votre commande est expédiée."


# --- The guards: where scanning the wrong string is a security bug -----------


def test_the_input_guard_moderates_a_blocks_message() -> None:
    """A hostile message must be blocked whatever shape the content arrives in.

    This is the case `str(.content)` handled by accident and could stop handling:
    the regexes matched only because the sentence happened to survive inside the
    repr. Nothing guaranteed it — a rule anchored on a sentence start (`^`) or on
    surrounding punctuation would have missed.
    """
    guard_input = make_guard_input(build_input_guard(4000))
    state = {"messages": [HumanMessage(content=blocks("Sale race, retournez dans votre pays."))]}

    result = guard_input(state)  # type: ignore[arg-type]

    assert result["input_blocked"] is True


def test_the_input_guard_lets_a_legitimate_blocks_message_through() -> None:
    """The other half: no false positive introduced by the normalisation."""
    guard_input = make_guard_input(build_input_guard(4000))
    state = {"messages": [HumanMessage(content=blocks("Ou est ma commande O-2024-0101 ?"))]}

    assert guard_input(state)["input_blocked"] is False  # type: ignore[arg-type]


def test_the_output_guard_replaces_a_harmful_blocks_reply() -> None:
    """The exit guard is the last thing between the model and the customer.

    Scanning a repr here means shipping a reply that was never really screened.
    """
    guard_output = make_guard_output(build_output_guard([]))
    state = {
        "messages": [
            AIMessage(content=blocks("Ces clients sont des sous-humains."), id="reply-1")
        ]
    }

    result = guard_output(state)  # type: ignore[arg-type]

    assert result["messages"][0].content == SAFE_OUTPUT_MESSAGE


def test_the_output_guard_redacts_a_secret_from_a_blocks_reply() -> None:
    """Redaction must reach the value, not stop at the wrapper."""
    guard_output = make_guard_output(build_output_guard([]))
    state = {
        "messages": [
            AIMessage(content=blocks("Le mot de passe est Velmo2024!."), id="reply-1")
        ]
    }

    sent = guard_output(state)["messages"][0].content  # type: ignore[arg-type]

    assert "Velmo2024" not in sent
    assert "mot de passe" in sent


def test_a_clean_blocks_reply_is_not_rewritten() -> None:
    """No update at all when nothing needs changing — including for blocks.

    Worth pinning: the node compares `decision.sanitized_text` to the message
    text, so a normalisation mismatch here would rewrite every reply on a blocks
    provider, quietly flattening content on each turn.
    """
    guard_output = make_guard_output(build_output_guard([]))
    state = {
        "messages": [
            AIMessage(content=blocks("Votre commande O-2024-0103 est expédiée."), id="r")
        ]
    }

    assert guard_output(state) == {}  # type: ignore[arg-type]


# --- The eval gate: the site the audit named --------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (AIMessage(content=blocks("Le retour est gratuit.")), "Le retour est gratuit."),
        (ToolMessage(content=blocks("order O-2024-0101: shipped"), tool_call_id="t"), "order O-2024-0101: shipped"),
    ],
)
def test_the_evaluators_receive_a_string_from_blocks_content(message, expected: str) -> None:
    """What `eval/run.py` extracts must be `.lower()`-able and `fold()`-able.

    The evaluators call both on it (`evaluators.py`), which is how Q2 turned a
    provider difference into a dead non-regression gate.
    """
    assert message.text == expected
    assert message.text.lower() == expected.lower()
