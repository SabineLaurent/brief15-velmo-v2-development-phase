"""The acceptance corpora, loaded as DATA (`data/eval/*.jsonl`).

Three JSONL files hold the acceptance criteria as data rather than prose, so this
project executes them instead of paraphrasing them:

    guardrail_cases.jsonl  (35)  -> driven by tests/test_moderation.py
    memory_cases.jsonl     (12)  -> driven by tests/test_memory_cases.py
    quality_cases.jsonl     (8)  -> driven by tests/test_quality_cases.py

They are copied VERBATIM, so a diff against the source stays meaningful and nobody can
quietly soften a criterion by editing the expectation instead of the code. Everything
decided ON TOP of them — accepted paraphrases, deviations under test — lives here in
code, never inside the corpus.

`load_quality_cases()` maps its rows into the same `{inputs, outputs}` shape as
`EVAL_CASES`, so the corpus goes through the same evaluators and the same two runners.
The other two loaders return their rows untouched: they are unit-level specs about the
memory layer and the guardrails, not agent runs.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CORPUS_NAMES = ("guardrail_cases", "memory_cases", "quality_cases")

# --- Where the corpora live -------------------------------------------------
_CORPUS_DIRNAME = Path("data") / "eval"


@lru_cache(maxsize=1)
def corpus_dir() -> Path:
    """Return `<repo root>/data/eval`, or raise with an actionable message."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CORPUS_DIRNAME
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"Could not locate {_CORPUS_DIRNAME} above {__file__}. The acceptance "
        "corpora ship with the repository, not with the installed package."
    )


def load_corpus(name: str) -> list[dict[str, Any]]:
    """Read one JSONL corpus into a list of rows, blank lines ignored."""
    path = corpus_dir() / f"{name}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@lru_cache(maxsize=1)
def corpus_fingerprint() -> str:
    """A short hash of the three corpora, as they are on disk right now.

    The easiest way to draw a false conclusion from an evaluation is to compare two
    scores that did not answer the same questions. The fingerprint travels inside
    `current_version()`, and a baseline recorded under a different fingerprint is
    refused rather than silently compared.

    Hashed as raw BYTES in a fixed order, so anything that changes the corpora at all —
    a line ending, a re-ordered row — changes the fingerprint.
    """
    digest = hashlib.sha256()
    for name in CORPUS_NAMES:
        digest.update((corpus_dir() / f"{name}.jsonl").read_bytes())
    return digest.hexdigest()[:12]


# --- The guardrail corpus ---------------------------------------------------

DELIBERATELY_NOT_BLOCKED = {"out_of_scope"}


def load_guardrail_cases() -> list[dict[str, Any]]:
    """35 cases: 23 that must be refused, 12 that must NOT be."""
    return load_corpus("guardrail_cases")


# --- The memory corpus ------------------------------------------------------


def load_memory_cases(tag: str | None = None) -> list[dict[str, Any]]:
    """12 cases tagged R1 / R2 / R3 / R5, optionally filtered by tag.

    Each row carries a scripted conversation (`turns`) plus an `evaluation`
    block whose `type` says what to check: `recall` (still in the fil),
    `persistence` (survives into a new session), or `forget` (gone, verifiably).
    """
    rows = load_corpus("memory_cases")
    return [row for row in rows if tag is None or row["tag"] == tag]


def memory_user_turns(case: dict[str, Any]) -> list[str]:
    """The customer's messages, in order."""
    return [turn["content"] for turn in case["turns"] if turn["role"] == "user"]


def memory_assistant_turns(case: dict[str, Any]) -> list[str]:
    """The agent's scripted replies, in order."""
    return [turn["content"] for turn in case["turns"] if turn["role"] == "assistant"]


# --- The quality corpus -----------------------------------------------------

ACCEPTED_PARAPHRASES: dict[str, tuple[str, ...]] = {
    "prepared": ("prepared", "prepare", "preparation"),
    "j+2": ("j+2", "2 jours ouvres", "deux jours ouvres"),
}

UNSUPPORTED_QUALITY_CASES = {"q-stock"}


def load_quality_cases(*, include_unsupported: bool = False) -> list[dict[str, Any]]:
    """8 support questions, mapped into the `EVAL_CASES` shape.

    The route is pinned to `support` for all of them: every one needs either the
    FAQ or a business tool, so `answer` (small talk) would be a routing
    regression. That check costs nothing and is the strongest deterministic
    signal we have, exactly as in `dataset.py`.
    """
    cases: list[dict[str, Any]] = []
    for row in load_corpus("quality_cases"):
        if row["id"] in UNSUPPORTED_QUALITY_CASES and not include_unsupported:
            continue
        cases.append(
            {
                "id": row["id"],
                "inputs": {"message": row["question"], "user_id": row["user_id"]},
                "outputs": {
                    "route": "support",
                    "expect_substring": row["expected_substring"],
                },
            }
        )
    return cases
