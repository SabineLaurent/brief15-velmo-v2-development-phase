"""Chantier 1: the starter's 12 memory cases, run as a spec.

`data/eval/memory_cases.jsonl` states R1/R2/R3/R5 as data — scripted
conversations plus what the agent must (or must not) be able to say afterwards.
It is executed here rather than paraphrased, the same way
`test_moderation.py` executes the guardrail corpus.

**Each tag is asserted against the mechanism that actually implements it**, which
is the one adaptation this port makes:

    R1  ->  the CHECKPOINTER, keyed by thread_id  (nothing is dropped, no search)
    R2  ->  the STORE,        keyed by user_id    (durable, survives the process)
    R3  ->  `memories_namespace`                  (one user cannot reach another)
    R5  ->  `forget_user_memories`                (deleted, and verified deleted)

In the starter all four go through one `MemoryManager` and R1 is a semantic
lookup; asserting R1 through a search here would test the store instead of the
fil, i.e. the wrong mechanism. Same reasoning as
`test_memory_requirements.py`, which holds the DEPTH of R1/R2 (30 turns,
recall by meaning against a competitor). This file adds the corpus's BREADTH:
ten more kinds of fact, and two pairs — the R3 pair and the R5 pair — that were
written to catch cross-contamination.

**And every durable case runs ONCE PER ENGINE** (chantier 8): SQLite always,
Postgres too when `EVAL_DATABASE_URL` offers one. R3 is the isolation between
customers — a security property — and the query that could hand back another
customer's row belongs to the store, not to us: sqlite-vec here, pgvector there.
Proving it on one engine says nothing about the other, and production is the other.

Offline by construction: no provider, no key, no network. The embeddings are a
deterministic bag-of-words over the corpus's own vocabulary, and the durable
storage is a temp directory of SQLite files (plus, when offered, throwaway
schemas in a real Postgres).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator

import pytest

from support_agent.eval.corpus import load_memory_cases, memory_user_turns
from support_agent.eval.offline import (
    EVAL_DATABASE_URL_ENV,
    FORGET_FLOOR,
    POSTGRES,
    R1_MIN_TURNS,
    SCORE_SCHEMA_PREFIX,
    SQLITE,
    TEST_SCHEMA_PREFIX,
    VOCAB,
    MemoryEngine,
    close_eval_pools,
    eval_postgres_url,
    purge_eval_schemas,
    remember_user_turns,
    replay_conversation,
    resume_history,
    score_memory,
)
from support_agent.memory.privacy import (
    forget_user_memories,
    list_user_memories,
    search_user_memories,
)

# The offline plumbing this module used to define — the deterministic bag-of-words
# embeddings, the 30-turn padding, the durable temp store, the erasure floor — now
# lives in `support_agent/eval/offline.py`, because the MLOps scorer needs exactly
# the same setup to COUNT what this file ASSERTS. One implementation, two
# consumers: had it been copied, the fake embeddings or the padding could drift on
# one side and the score would stop meaning what these tests prove.
#
# The division of labour, and it is deliberate:
#   this file      asserts — one case per test, rich diagnostics on failure
#   eval/mlops.py  counts  — a boolean per case, never raises
# `tests/test_mlops.py` then requires the memory dimension to be perfect on EVERY
# engine it scored, so a scorer predicate that drifted away from these assertions
# fails CI instead of quietly reporting a pass.


# --- One engine per parameter -------------------------------------------------

_POSTGRES_URL = eval_postgres_url()

# Schema names must be unique for the whole SESSION, because they cannot be
# recycled: `get_postgres_pool` is `lru_cache`d, so dropping a schema mid-session
# would leave a live pool pointing at nothing (see `purge_eval_schemas`). A
# counter is enough — and `_purge_stale_schemas` below is what keeps yesterday's
# names from piling up in a developer's database.
_slot_numbers = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def _purge_stale_schemas() -> Iterator[None]:
    """Reset what previous sessions left behind, and close our pools at the end.

    The teardown is not tidiness: one pool per Postgres-parametrised test, each
    with its own worker threads and none of them closed, grows with the corpus and
    ends in `too many clients` on a wider one.
    """
    if _POSTGRES_URL:
        purge_eval_schemas(_POSTGRES_URL, TEST_SCHEMA_PREFIX)
    yield
    close_eval_pools()


@pytest.fixture(params=[SQLITE, POSTGRES])
def engine(request: pytest.FixtureRequest, tmp_path) -> MemoryEngine:
    """A durable engine of its own for one test: files, or throwaway schemas.

    Postgres SKIPS rather than fails when no URL is configured: `make test` on a
    laptop must not require a database. What stops that skip from becoming the
    silent half-coverage this chantier fixes is on the other side —
    `make score ARGS=--require-postgres` in CI, which goes red when the engine
    it demands did not run.
    """
    if request.param == POSTGRES:
        if not _POSTGRES_URL:
            pytest.skip(f"{EVAL_DATABASE_URL_ENV} non défini : pas de Postgres à noter")
        return MemoryEngine(
            POSTGRES,
            tmp_path,
            database_url=_POSTGRES_URL,
            # The unique part lives in the PREFIX, so tests can keep asking for a
            # readable slot name ("memories", "fil") and still never collide.
            schema_prefix=f"{TEST_SCHEMA_PREFIX}{next(_slot_numbers)}_",
        )
    return MemoryEngine(SQLITE, tmp_path)


# --- The harness itself, on the engine that has a server ----------------------


@pytest.mark.skipif(not _POSTGRES_URL, reason=f"{EVAL_DATABASE_URL_ENV} non défini")
def test_scoring_twice_in_one_process_keeps_every_slot_in_its_own_schema(tmp_path) -> None:
    """The purge must RESET its schemas, never merely drop them.

    The bug this pins down, found by review and reproduced before the fix: the
    purge runs on every `score_memory`, while `get_postgres_pool` is `lru_cache`d.
    So the SECOND scoring run in a process dropped the schemas that the first
    run's still-cached pools were bound to. `configure` does not re-run on a cache
    hit, so nothing recreated them — and Postgres silently ignores a missing entry
    in `search_path`, so every slot's tables were created in `public` instead.

    What made it dangerous is that it did not fail: all eleven slots shared one
    physical store and the corpus still scored 24/24, because
    `memories_namespace(user_id)` was carrying the isolation by itself. A green
    run with the per-case isolation silently gone is precisely what this chantier
    exists to prevent, so the assertion is on the STORAGE, not on the score.

    `run_eval()` calls `score_memory` twice per pytest process (its `scores` and
    `degraded` fixtures), so this is the real configuration, not a contrived one.
    """
    import psycopg

    first = score_memory(tmp_path / "run1")
    second = score_memory(tmp_path / "run2")
    assert first.score == 1.0
    assert second.score == 1.0, [f.case_id for f in second.failures]

    # The seven slots that hold a long-term store (R2 ×4, R3, R5 ×2) must each
    # still own their `store` table. Under the bug these schemas did not merely
    # lose their tables — they no longer existed at all.
    with psycopg.connect(_POSTGRES_URL, autocommit=True) as conn:
        owners = conn.execute(
            "SELECT schemaname FROM pg_catalog.pg_tables "
            "WHERE tablename = 'store' AND left(schemaname, %s) = %s",
            (len(SCORE_SCHEMA_PREFIX), SCORE_SCHEMA_PREFIX),
        ).fetchall()

    assert len(owners) == 7, (
        f"expected one store per slot schema, got {sorted(row[0] for row in owners)} — "
        "a slot whose schema vanished writes into `public`, and every slot then "
        "shares one store"
    )


# --- The corpus is the spec -------------------------------------------------


def test_the_corpus_is_the_size_and_shape_we_ported() -> None:
    """A changed corpus needs a look, not a silent pass."""
    cases = load_memory_cases()
    assert len(cases) == 12
    assert [len(load_memory_cases(tag)) for tag in ("R1", "R2", "R3", "R5")] == [4, 4, 2, 2]
    # Every case must be reachable by a tag: an untagged row would be collected,
    # never asserted, and look covered.
    assert sum(len(load_memory_cases(t)) for t in ("R1", "R2", "R3", "R5")) == len(cases)


def test_every_searched_fact_is_visible_to_the_fake_embeddings() -> None:
    """Guard against the corpus drifting out of `VOCAB`.

    A fact with no vocabulary word gets the neutral vector, so it becomes
    equidistant from every query — the search would still "work" while measuring
    nothing. Without this check, adding a case could silently turn an assertion
    below into a coin flip.

    Scoped to the tags that SEARCH (R2/R3/R5). R1 lives in the checkpointer, which
    has no embeddings at all: including it would fail on sentences whose
    retrievability is irrelevant ("Commence par le Bresil.") and the honest fix
    would then be to pad `VOCAB` with words no assertion uses.
    """
    blind = [
        text
        for tag in ("R2", "R3", "R5")
        for case in load_memory_cases(tag)
        for text in memory_user_turns(case)
        if not any(word in text.lower() for word in VOCAB)
    ]
    assert not blind, f"no vocabulary word in: {blind}"


# --- R1: hold the fil of a 30-turn conversation ------------------------------


@pytest.mark.parametrize(
    "case", load_memory_cases("R1"), ids=[c["id"] for c in load_memory_cases("R1")]
)
def test_R1_the_first_turn_survives_a_long_conversation(
    case: dict, engine: MemoryEngine
) -> None:
    """R1: what the customer said early is still in the fil 30 turns later.

    Thirty-odd separate `invoke` calls, not one call with a long list — that is
    the difference between "the graph can hold a list" and "the agent remembers
    the discussion", which is what the requirement asks for.
    """
    graph, config, turns = replay_conversation(
        case, engine.checkpointer("fil"), pad_to=R1_MIN_TURNS
    )

    history = graph.get_state(config).values["messages"]
    # Each turn contributes the customer message + the agent reply.
    assert len(history) == 2 * turns
    assert turns >= R1_MIN_TURNS

    expected = case["evaluation"]["expected_substring"]
    assert any(expected in m.text for m in history), (
        f"[{case['id']}] {expected!r} fell out of a {turns}-turn fil"
    )


@pytest.mark.parametrize(
    "case", load_memory_cases("R1"), ids=[c["id"] for c in load_memory_cases("R1")]
)
def test_R1_a_long_conversation_resumes_on_a_new_saver(
    case: dict, engine: MemoryEngine
) -> None:
    """R1 across a restart: the ENGINE holds the fil, not the process.

    A new graph over a NEW saver object on the same storage — the same shape as
    R5's "gone_from_disk", and the strongest form this can take without spawning
    a second process. It used to be a new graph over the SAME in-memory saver,
    which could only ever prove that an object still held its own dict; now that
    both engines are durable (`MemoryEngine.checkpointer`), the restart is real
    on both, and it is `PostgresSaver`'s round trip that had never been asserted
    anywhere before.
    """
    _, config, turns = replay_conversation(
        case, engine.checkpointer("fil"), pad_to=R1_MIN_TURNS
    )

    resumed = resume_history(engine.checkpointer("fil"), config)

    assert len(resumed) == 2 * turns, f"[{case['id']}] the fil did not survive the saver"
    expected = case["evaluation"]["expected_substring"]
    assert any(expected in m.text for m in resumed)


# --- R2: remember durable facts from one session to the next ------------------


@pytest.mark.parametrize(
    "case", load_memory_cases("R2"), ids=[c["id"] for c in load_memory_cases("R2")]
)
def test_R2_a_durable_fact_survives_into_a_new_session(
    case: dict, engine: MemoryEngine
) -> None:
    """R2: a second session, days later, finds what the first one learned.

    Two independent store objects over the same storage — the second never sees
    the first, which is exactly the situation "the customer comes back next week"
    puts the agent in.

    The assertion is PRESENCE in the recalled set, not rank. Ranking under fake
    bag-of-words embeddings would measure the fake ("statut de compte" shares no
    word with "je suis revendeur", so a real model ranks it and this one cannot),
    and dressing that up would test the test. The rank property is asserted where
    it is fair — with a real competitor — in `test_memory_requirements.py`.
    """
    session1 = engine.store("memories")
    remember_user_turns(session1, case)

    session2 = engine.store("memories")
    recalled = " ".join(
        record.text
        for record in search_user_memories(
            session2, case["user_id"], case["evaluation"]["question"], limit=10
        )
    )

    expected = case["evaluation"]["expected_substring"]
    assert expected in recalled, f"[{case['id']}] {expected!r} not recalled from {recalled!r}"


# --- R3: strict isolation between users --------------------------------------


def test_R3_neither_user_of_the_pair_can_reach_the_others_fact(
    engine: MemoryEngine,
) -> None:
    """R3, on the pair the corpus built for it, on EVERY engine.

    The two cases are the same sentence with a different order number, stored for
    two different customers. Under bag-of-words embeddings the two facts have
    IDENTICAL vectors, so similarity cannot tell them apart — the only thing
    keeping them separate is `memories_namespace(user_id)`. That is what makes
    this pair worth executing rather than one isolation test with distinct text:
    it removes the possibility of passing by lexical luck.

    And it is the reason the whole engine parameter exists. `memories_namespace`
    is ours and shared; the vector search that could return the neighbour's row
    is the store's own. This test on `sqlite` says nothing about `pgvector`, and
    pgvector is what will hold the real customers.
    """
    store = engine.store("memories")
    cases = load_memory_cases("R3")
    assert len(cases) == 2
    for case in cases:
        remember_user_turns(store, case)

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
        assert mine in recalled, f"[{case['id']}] lost its own fact"
        assert theirs not in recalled, f"[{case['id']}] LEAKED {theirs!r} from {other['user_id']}"

        # And the audit dump — the other way in, and the one an operator uses.
        dumped = " ".join(r.text for r in list_user_memories(store, case["user_id"]))
        assert mine in dumped and theirs not in dumped


# --- R5: the right to be forgotten ------------------------------------------


@pytest.mark.parametrize(
    "case", load_memory_cases("R5"), ids=[c["id"] for c in load_memory_cases("R5")]
)
def test_R5_the_forgotten_fact_is_gone_and_verifiably_so(
    case: dict, engine: MemoryEngine
) -> None:
    """R5: "oublie mon adresse" deletes it, and the deletion is checkable.

    Three ways of looking, because a deletion that only holds for one of them is
    not a deletion: the semantic recall the agent uses, the audit dump an operator
    uses, and the return value that says what was destroyed.

    `forget_user_memories` re-reads the keys after deleting them and raises if any
    survive, so a silently swallowed write fails here loudly rather than being
    reported as a successful erasure.
    """
    store = engine.store("memories")
    remember_user_turns(store, case)
    user_id = case["user_id"]
    forbidden = case["evaluation"]["forbidden_substring"]

    # It really was there — otherwise the assertions below pass on an empty store.
    assert any(forbidden in r.text for r in list_user_memories(store, user_id))

    deleted = forget_user_memories(
        store, user_id, case["evaluation"]["target"], min_score=FORGET_FLOOR
    )

    assert deleted, f"[{case['id']}] nothing matched {case['evaluation']['target']!r}"
    assert any(forbidden in r.text for r in deleted), "deleted the wrong fact"
    assert not any(forbidden in r.text for r in list_user_memories(store, user_id))
    recalled = search_user_memories(store, user_id, case["evaluation"]["question"], limit=10)
    assert not any(forbidden in r.text for r in recalled)


@pytest.mark.parametrize(
    "case", load_memory_cases("R5"), ids=[c["id"] for c in load_memory_cases("R5")]
)
def test_R5_the_erasure_does_not_outlive_the_process(
    case: dict, engine: MemoryEngine
) -> None:
    """The half of R5 a same-process assertion cannot see: it hit the STORAGE.

    Since Phase 10 the store is durable, so "deleted" must mean deleted in the
    file (or in the schema) — an in-process cache that merely stopped returning
    the row would satisfy the test above and hand the fact back after a restart.
    """
    session1 = engine.store("memories")
    remember_user_turns(session1, case)
    forget_user_memories(
        session1, case["user_id"], case["evaluation"]["target"], min_score=FORGET_FLOOR
    )

    session2 = engine.store("memories")
    survivors = list_user_memories(session2, case["user_id"])

    forbidden = case["evaluation"]["forbidden_substring"]
    assert not any(forbidden in r.text for r in survivors)
