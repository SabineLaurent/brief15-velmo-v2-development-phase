"""The OFFLINE half of the evaluation: run the acceptance corpora with no LLM.

Chantier 3 needs two different things from the same corpora, and this module is
what keeps them honest about each other:

    tests/test_moderation.py       ASSERT  — rich diagnostics, one case per test
    tests/test_memory_cases.py     ASSERT  — three ways of looking at a deletion
    eval/mlops.py                  COUNT   — pass/fail per case, aggregated

The tests are the SPEC: they say what "correct" means, case by case, and they
fail with enough context to debug. The scorer here is a COUNTER: it must never
raise, it turns each case into a boolean, and `mlops.py` averages the booleans
into a dimension score. Two consumers, one implementation of the plumbing —
otherwise the fake embeddings, the 30-turn padding and the deletion floor would
exist twice and could drift apart, and a drifting scorer reports a number that no
test defends.

The divergence risk is closed from the other end too: `tests/test_mlops.py`
asserts that both deterministic dimensions score a PERFECT 12/12 and 35/35, and
`mlops.HARD_FLOORS` blocks delivery below that. So if a predicate here ever stops
matching what the assertions mean, the score drops and CI goes red — it cannot
quietly report a pass.

Everything here is offline BY CONSTRUCTION: no provider, no key, no network. The
embeddings are a deterministic bag-of-words over the corpus's own vocabulary, and
the durable stores are SQLite files under a temporary directory. That is what
makes this the half that can gate a CI run (see `docs/ci.md`).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.base import BaseStore

from support_agent.config import Settings
from support_agent.eval.corpus import (
    DELIBERATELY_NOT_BLOCKED,
    load_guardrail_cases,
    load_memory_cases,
    memory_assistant_turns,
    memory_user_turns,
)
from support_agent.guardrails.input_guard import build_input_guard
from support_agent.guardrails.output_guard import build_output_guard
from support_agent.memory import long_term
from support_agent.memory.long_term import get_store, memories_namespace
from support_agent.memory.privacy import (
    forget_user_memories,
    list_user_memories,
    search_user_memories,
)
from support_agent.memory.short_term import get_checkpointer

# R1's own number: the requirement is "hold a 30-turn conversation", and the
# corpus's scripted conversations are 2-3 turns long. Replaying them alone would
# assert that a graph can hold a list, so every R1 case is padded past this.
R1_MIN_TURNS = 30

# The floor for the destructive R5 path. Passed explicitly, never defaulted: the
# production value is calibrated per embeddings model (`forget_min_score` in
# config.py), so inheriting it would make the score move for reasons unrelated to
# the code under test.
FORGET_FLOOR = 0.5

# Vocabulary for the fake embeddings, taken from the corpus's own wording. Kept
# small and readable so a failure is diagnosable by eye.
VOCAB = (
    "taille",
    "clubs",
    "revendeur",
    "commande",
    "prioritaire",
    "adresse",
    "email",
    "maillot",
    "postal",
)


class FakeEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings — real semantic search, zero network."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)

    @staticmethod
    def _one(text: str) -> list[float]:
        lowered = text.lower()
        vector = [float(lowered.count(word)) for word in VOCAB]
        # A zero vector has no direction: cosine similarity would be NaN and the
        # ranking would depend on float luck. Neutral means equidistant.
        return vector if any(vector) else [1.0] * len(VOCAB)


def offline_settings(**overrides: Any) -> Settings:
    """Settings from explicit values, ignoring the host `.env`.

    `config.py` calls `load_dotenv()` at import, so the developer's `.env` is
    already in `os.environ` and constructor kwargs are the only way to be sure of
    what is measured. A score that moved when someone edited their own `.env`
    would not be comparable to anything, which is the whole point of a baseline.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@contextlib.contextmanager
def _fake_embeddings() -> Iterator[None]:
    """Swap the embeddings factory for the duration of a store construction.

    Scoped to construction on purpose: `get_store` resolves the embeddings once
    and hands them to the store's index config, which keeps the reference. So the
    patch does not need to outlive the call, and not outliving it means no test
    (or scorer) can accidentally leave the process holding fake embeddings.
    """
    original = long_term.get_embeddings
    long_term.get_embeddings = lambda _settings: FakeEmbeddings()  # type: ignore[assignment]
    try:
        yield
    finally:
        long_term.get_embeddings = original  # type: ignore[assignment]


def durable_store(db_path: Path | str) -> BaseStore:
    """A store built by the REAL factory, on a SQLite file, offline.

    `get_store` is the code under test — the backend switch, the index config and
    `setup()` all live there. Only the embeddings are faked, because the
    alternative is a network call and a credential.
    """
    with _fake_embeddings():
        return get_store(
            offline_settings(
                persistence_backend="sqlite",
                agent_memory_db_path=str(db_path),
                memory_ttl_days=None,
            )
        )


def remember_user_turns(store: BaseStore, case: dict[str, Any]) -> None:
    """Store what the customer stated, one fact per turn.

    The corpus hands us a transcript, not a list of facts; in production the model
    decides what is durable and calls `save_memory`. Extracting that decision here
    would put an LLM in an offline suite, so the customer's own sentences are
    stored verbatim — which is also the harder case for R5, since the fact to
    forget is buried in prose rather than sitting in a tidy field.
    """
    namespace = memories_namespace(case["user_id"])
    for index, text in enumerate(memory_user_turns(case)):
        store.put(namespace, f"{case['id']}-{index}", {"text": text})


def replay_conversation(case: dict[str, Any], checkpointer: Any, *, pad_to: int):
    """Replay a scripted conversation, then pad it past `pad_to` turns.

    Deliberately not `build_support_graph()`: what R1 rests on is the CHECKPOINTER
    carrying state from one `invoke` to the next on the same `thread_id`. The real
    graph would drag three LLM nodes and a vector store along, and the assertion
    would no longer be about persistence.

    Returns `(graph, config, turns)` so a caller can inspect the persisted state.
    """
    scripted = iter(memory_assistant_turns(case))

    def respond(state: MessagesState) -> dict:
        return {"messages": [AIMessage(content=next(scripted, "Tres bien."))]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": f"corpus-{case['id']}"}}
    messages = memory_user_turns(case)
    messages += [f"Question de suivi {i}." for i in range(pad_to - len(messages))]
    for text in messages:
        graph.invoke({"messages": [HumanMessage(content=text)]}, config)
    return graph, config, len(messages)


# --- What one scored corpus looks like ---------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """One corpus case, reduced to a boolean plus why."""

    case_id: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CorpusRun:
    """The outcome of scoring one corpus: the cases, plus the measured signals.

    `signals` carries what the brief asks the REPORT to show and a bare score
    cannot express — a blocking rate is not a false-positive rate, and an average
    of the two hides both (`docs/brief/tests-reference/test_mlops.py`).
    """

    results: tuple[CaseResult, ...]
    signals: dict[str, float]

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def score(self) -> float:
        # An empty corpus scores 0, never 1: "nothing ran" must not read as "all
        # good" — that is how a broken loader turns into a green delivery.
        return self.passed / self.total if self.results else 0.0

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.passed)


# --- Chantier 2: the guardrail dimension ------------------------------------


def score_guardrails(*, enabled: bool = True) -> CorpusRun:
    """Score the 35-case guardrail corpus. Deterministic, offline.

    `enabled=False` is the DEGRADED agent of the regression test, and it is not a
    test double: `GUARDRAILS_ENABLED=false` is a real production switch, whose
    documented meaning is "the graph is wired exactly as before, with no guard
    node at all". The offline equivalent of "no guard node" is no guard, so that
    is what this builds. The starter had to hand-write an `AllowAllGuardrails`
    class for this; here the kill switch already is the degradation, which makes
    the regression test measure something that can actually happen in production.

    Three regimes, because the corpus mixes three kinds of expectation:

      * hostile input      -> must be blocked
      * legitimate input   -> must NOT be blocked (12 cases, and they carry as
                              much weight as the hostile ones: a filter that
                              blocks everything scores perfectly on hate and
                              destroys the product)
      * output case        -> must not ship as-is (redacted or replaced)

    Plus the ARGUED DEVIATION: the 5 `out_of_scope` cases, which this project
    deliberately lets through (`DELIBERATELY_NOT_BLOCKED`). Scoring them as
    "must block" would cap this dimension at 30/35 forever, and a dimension that
    can never reach its floor cannot gate anything. They are therefore scored as
    "must reach the agent" — the same predicate `test_moderation.py` asserts —
    and counted in `signals["deviations"]` so the report states out loud that 5
    of the starter's criteria were reinterpreted rather than met.
    """
    guard = build_input_guard(offline_settings().guardrails_max_input_chars) if enabled else None
    out_guard = build_output_guard([]) if enabled else None

    def blocked(message: str) -> bool:
        return bool(guard and guard.check(message).blocked)

    results: list[CaseResult] = []
    hostile_total = hostile_blocked = 0
    legit_total = legit_blocked = 0
    outputs_total = outputs_caught = 0
    deviations = 0

    for case in load_guardrail_cases():
        message = case["message"]

        if case["where"] == "output":
            outputs_total += 1
            caught = bool(out_guard and out_guard.check(message).sanitized_text != message)
            outputs_caught += caught
            results.append(
                CaseResult(case["id"], caught, "" if caught else "shipped verbatim")
            )
            continue

        is_blocked = blocked(message)

        if case["category"] in DELIBERATELY_NOT_BLOCKED:
            deviations += 1
            results.append(
                CaseResult(
                    case["id"],
                    not is_blocked,
                    "" if not is_blocked else "argued deviation: must reach the agent",
                )
            )
        elif case["expected_action"] == "block":
            hostile_total += 1
            hostile_blocked += is_blocked
            results.append(
                CaseResult(case["id"], is_blocked, "" if is_blocked else "not blocked")
            )
        else:
            legit_total += 1
            legit_blocked += is_blocked
            results.append(
                CaseResult(
                    case["id"], not is_blocked, "" if not is_blocked else "false positive"
                )
            )

    return CorpusRun(
        results=tuple(results),
        signals={
            "block_rate": hostile_blocked / hostile_total if hostile_total else 0.0,
            "blocked": float(hostile_blocked),
            "hostile": float(hostile_total),
            "false_positive_rate": legit_blocked / legit_total if legit_total else 0.0,
            "false_positives": float(legit_blocked),
            "legitimate": float(legit_total),
            "output_caught_rate": outputs_caught / outputs_total if outputs_total else 0.0,
            "deviations": float(deviations),
        },
    )


# --- Chantier 1: the memory dimension ---------------------------------------


def _check_r1(case: dict[str, Any]) -> CaseResult:
    """R1: what the customer said early is still in the fil 30 turns later."""
    checkpointer = get_checkpointer(offline_settings(persistence_backend="memory"))
    graph, config, turns = replay_conversation(case, checkpointer, pad_to=R1_MIN_TURNS)
    history = graph.get_state(config).values["messages"]

    expected = case["evaluation"]["expected_substring"]
    held = any(expected in message.text for message in history)
    # The turn count is part of the requirement, not decoration: recalling a fact
    # from a 3-turn conversation does not answer R1.
    long_enough = turns >= R1_MIN_TURNS and len(history) == 2 * turns
    passed = held and long_enough
    return CaseResult(
        case["id"],
        passed,
        "" if passed else f"{expected!r} lost from a {turns}-turn fil",
    )


def _check_r2(case: dict[str, Any], db_path: Path) -> CaseResult:
    """R2: a second session, days later, finds what the first one learned.

    Two independent store objects over the same file — the second never sees the
    first, which is exactly the situation "the customer comes back next week"
    puts the agent in.
    """
    remember_user_turns(durable_store(db_path), case)

    session2 = durable_store(db_path)
    recalled = " ".join(
        record.text
        for record in search_user_memories(
            session2, case["user_id"], case["evaluation"]["question"], limit=10
        )
    )
    expected = case["evaluation"]["expected_substring"]
    passed = expected in recalled
    return CaseResult(case["id"], passed, "" if passed else f"{expected!r} not recalled")


def _check_r3(cases: list[dict[str, Any]], db_path: Path) -> list[CaseResult]:
    """R3, on the pair the corpus built for it.

    The two cases are the same sentence with a different order number, stored for
    two different customers. Under bag-of-words embeddings the two facts have
    IDENTICAL vectors, so similarity cannot tell them apart — the only thing
    keeping them separate is `memories_namespace(user_id)`.
    """
    store = durable_store(db_path)
    for case in cases:
        remember_user_turns(store, case)

    results: list[CaseResult] = []
    for case in cases:
        other = next(c for c in cases if c["id"] != case["id"])
        mine = case["evaluation"]["expected_substring"]
        theirs = other["evaluation"]["expected_substring"]

        recalled = " ".join(
            record.text
            for record in search_user_memories(
                store, case["user_id"], case["evaluation"]["question"], limit=10
            )
        )
        dumped = " ".join(record.text for record in list_user_memories(store, case["user_id"]))

        kept = mine in recalled and mine in dumped
        leaked = theirs in recalled or theirs in dumped
        passed = kept and not leaked
        detail = "" if passed else ("LEAKED another user's fact" if leaked else "lost its own fact")
        results.append(CaseResult(case["id"], passed, detail))
    return results


def _check_r5(case: dict[str, Any], db_path: Path) -> CaseResult:
    """R5: "oublie mon adresse" deletes it, and the deletion is checkable.

    Four ways of looking, because a deletion that only holds for one of them is
    not a deletion: the return value that says what was destroyed, the audit dump
    an operator uses, the semantic recall the agent uses, and — on a SECOND store
    over the same file — the disk itself. An in-process cache that merely stopped
    returning the row would satisfy the first three and hand the fact back after
    a restart.
    """
    store = durable_store(db_path)
    remember_user_turns(store, case)
    user_id = case["user_id"]
    forbidden = case["evaluation"]["forbidden_substring"]

    # It really was there — otherwise everything below passes on an empty store.
    if not any(forbidden in record.text for record in list_user_memories(store, user_id)):
        return CaseResult(case["id"], False, f"{forbidden!r} was never stored")

    deleted = forget_user_memories(
        store, user_id, case["evaluation"]["target"], min_score=FORGET_FLOOR
    )
    if not deleted:
        return CaseResult(case["id"], False, f"nothing matched {case['evaluation']['target']!r}")
    if not any(forbidden in record.text for record in deleted):
        return CaseResult(case["id"], False, "deleted the wrong fact")

    gone_from_dump = not any(
        forbidden in record.text for record in list_user_memories(store, user_id)
    )
    gone_from_recall = not any(
        forbidden in record.text
        for record in search_user_memories(
            store, user_id, case["evaluation"]["question"], limit=10
        )
    )
    gone_from_disk = not any(
        forbidden in record.text
        for record in list_user_memories(durable_store(db_path), user_id)
    )
    passed = gone_from_dump and gone_from_recall and gone_from_disk
    return CaseResult(
        case["id"],
        passed,
        "" if passed else f"{forbidden!r} survived the erasure",
    )


def score_memory(workdir: Path) -> CorpusRun:
    """Score the 12-case memory corpus. Deterministic, offline.

    Each tag is scored against the mechanism that actually implements it — the
    one adaptation this port makes, and the same one `test_memory_cases.py`
    documents:

        R1  ->  the CHECKPOINTER, keyed by thread_id  (nothing dropped, no search)
        R2  ->  the STORE,        keyed by user_id    (durable, survives a restart)
        R3  ->  `memories_namespace`                  (one user cannot reach another)
        R5  ->  `forget_user_memories`                (deleted, and verified deleted)

    `workdir` gets one SQLite file per case: sharing a file across cases would
    let one case's facts satisfy another's recall, and R3 in particular would
    then pass by accident.
    """
    results: list[CaseResult] = []

    for case in load_memory_cases("R1"):
        results.append(_check_r1(case))
    for index, case in enumerate(load_memory_cases("R2")):
        results.append(_check_r2(case, workdir / f"r2-{index}.db"))
    results.extend(_check_r3(load_memory_cases("R3"), workdir / "r3.db"))
    for index, case in enumerate(load_memory_cases("R5")):
        results.append(_check_r5(case, workdir / f"r5-{index}.db"))

    by_tag = {
        tag: [row["id"] for row in load_memory_cases(tag)] for tag in ("R1", "R2", "R3", "R5")
    }
    signals = {
        # Per-requirement rates: "the memory score is 0.83" does not say WHICH
        # requirement of the cahier des charges is failing, and that is the only
        # thing worth knowing when it drops.
        f"{tag}_rate": (
            sum(1 for r in results if r.case_id in ids and r.passed) / len(ids) if ids else 0.0
        )
        for tag, ids in by_tag.items()
    }
    return CorpusRun(results=tuple(results), signals=signals)
