"""The starter's acceptance corpora, loaded as DATA (`data/eval/*.jsonl`).

Three JSONL files came with the training starter. They are the closest thing the
briefs have to a cahier des charges — criteria written as data rather than prose —
so this project executes them instead of paraphrasing them:

    guardrail_cases.jsonl  (35)  -> chantier 2, driven by tests/test_moderation.py
    memory_cases.jsonl     (12)  -> chantier 1, driven by tests/test_memory_cases.py
    quality_cases.jsonl     (8)  -> chantier 3, driven by tests/test_quality_cases.py

They are copied VERBATIM (byte-identical to the starter): a diff against the
source stays meaningful, and nobody can quietly soften a criterion by editing the
expectation instead of the code. Everything this project decides ON TOP of them —
accepted paraphrases, deviations under test — lives here in code, where it is
reviewable, never inside the corpus.

`load_quality_cases()` maps its rows into the same `{inputs, outputs}` shape as
`EVAL_CASES` (`dataset.py`), so the corpus goes through the same evaluators and
the same two runners. The other two loaders return their rows untouched: they are
unit-level specs about the memory layer and the guardrails, not agent runs.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# The three files, named once. Order is fixed because `corpus_fingerprint()`
# hashes them in sequence: a set would make the fingerprint depend on iteration
# order and two identical checkouts could disagree.
CORPUS_NAMES = ("guardrail_cases", "memory_cases", "quality_cases")

# --- Where the corpora live -------------------------------------------------
#
# Resolved by walking UP from this file rather than from the cwd or a Settings
# field, because these files are repo material, not runtime configuration: they
# are consumed by pytest and by `make eval`, never by the served agent, and they
# are deliberately absent from the container image (unlike `data/kb-velmo`, which
# the agent needs at runtime).
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

    This exists because of the single easiest way to draw a false conclusion from
    an evaluation: comparing two scores that did not answer the same questions.
    A note is only comparable against another note computed over the SAME cases,
    so the fingerprint travels inside `current_version()` (`eval/mlops.py`) and a
    baseline recorded under a different fingerprint is refused rather than
    silently compared.

    Hashed as raw BYTES, in a fixed order: the corpora are byte-identical copies
    of the starter's files, so anything that changes them at all — including a
    line ending or a re-ordered row — must change the fingerprint.
    """
    digest = hashlib.sha256()
    for name in CORPUS_NAMES:
        digest.update((corpus_dir() / f"{name}.jsonl").read_bytes())
    return digest.hexdigest()[:12]


# --- Chantier 2: the guardrail corpus ---------------------------------------

# Categories the starter expects blocked that this project deliberately does NOT
# block. It lives HERE, with the other decisions taken on top of the corpora,
# because three consumers now need the same list: the assertions
# (`tests/test_moderation.py`), the scorer (`eval/offline.py`), and the report
# that has to state the deviation out loud (`eval/mlops.py`). A second
# hand-written copy would let the scorer and the tests disagree about what
# "correct" means, and the score would be the one that lies.
#
# The argument, in short: an honest "the FAQ does not cover that" serves the
# customer better than a hard block on an adjacent business question. The full
# version — including the part that is NOT settled, the two advice cases the
# brief names explicitly — is in `tests/test_moderation.py` and ROADMAP §12-D.
DELIBERATELY_NOT_BLOCKED = {"out_of_scope"}


def load_guardrail_cases() -> list[dict[str, Any]]:
    """35 cases: 23 that must be refused, 12 that must NOT be."""
    return load_corpus("guardrail_cases")


# --- Chantier 1: the memory corpus ------------------------------------------


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


# --- Chantier 3: the quality corpus -----------------------------------------

# The corpus states each expectation as ONE token. Most are FACTS — a price
# (`6,90`), a window (`14 jours`), a carrier (`Colissimo`) — and any correct answer
# contains them whatever the phrasing. Two are NOTATIONS of a fact, and a correct
# answer legitimately expands them:
#
#   `prepared`  is the BACKEND's English status word. `suivi-commande.md`
#               ENUMERATES the French status vocabulary of the domain ("payée,
#               préparée, expédiée, livrée, annulée…"), and the model maps onto it
#               — which is better behaviour than echoing an English enum at a
#               French customer.
#   `J+2`       is trade notation. `delais-livraison.md` itself glosses it as
#               "(environ 2 jours ouvrés)", so a model relaying the FAQ in prose
#               writes the gloss.
#
# Both alternative sets are therefore grounded in `data/kb-velmo`, not invented to
# make a red test green, and neither can be satisfied by a WRONG answer.
#
# ⚠️ MEASURED FLAKINESS, and the reason this list must stop growing. Across two
# consecutive live runs the same case came back as "en préparation" then "est
# préparée" — the FACT never wavered, only its surface. Substring scoring of
# free-form prose is unstable by construction; it is the `honest_refusal` disease
# (`f404775`, and the flake recorded in ROADMAP §Phase 9). Two of seven
# expectations needing an entry is the measured argument for a SEMANTIC JUDGE
# (chantier 3), not for a longer list. A red on these two is a coin toss to
# re-run, not a regression to investigate.
ACCEPTED_PARAPHRASES: dict[str, tuple[str, ...]] = {
    # "prepare" covers the participle forms ("préparée", "préparé") after fold();
    # "preparation" is the noun, which does NOT contain "prepare".
    "prepared": ("prepared", "prepare", "preparation"),
    "j+2": ("j+2", "2 jours ouvres", "deux jours ouvres"),
}

# The one case this project cannot answer, and why. The business port
# (`actions/backend.py`) exposes order lookup, ticket creation and ticket
# listing — there is no stock/availability capability, so `q-stock` has nothing
# to read. Named here rather than dropped, and pinned by a structural test
# (`test_quality_cases.py`), so adding availability later FAILS that test and
# forces this case back into the run instead of leaving it forgotten.
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
