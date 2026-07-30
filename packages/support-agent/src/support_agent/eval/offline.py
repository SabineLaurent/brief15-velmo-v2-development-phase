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
the durable engines are a temporary directory of SQLite files plus — when one is
offered — a real Postgres (see `MemoryEngine`). That is what makes this the half
that can gate a CI run (see `docs/ci.md`).
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
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
from support_agent.memory.postgres_conn import get_postgres_pool
from support_agent.memory.privacy import (
    forget_user_memories,
    list_user_memories,
    search_user_memories,
)
from support_agent.memory.short_term import get_checkpointer

logger = logging.getLogger(__name__)

# Every (url, schema) this harness has caused a Postgres pool to be opened for.
# Needed because `lru_cache` exposes no keys, and `close_eval_pools` has to know
# what to hand back to itself in order to close it.
_opened_postgres_pools: set[tuple[str, str]] = set()

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


# --- The engines the durable half of the corpus is scored against -------------
#
# WHY THIS IS NOT ONE HARD-CODED BACKEND. Until this existed, the memory
# dimension was computed with `persistence_backend="sqlite"`, full stop. So
# "mémoire 12/12" was true — ON SQLITE. Production is a single Postgres with
# pgvector (`docs/architecture-cible-2026-07-25.md`), and R3 is not a convenience
# feature: it is the ISOLATION BETWEEN CUSTOMERS, a security property. What keeps
# two customers apart is `memories_namespace(user_id)` — our code, shared by both
# engines — but the SIMILARITY QUERY that could hand back somebody else's row
# belongs to the store (sqlite-vec here, pgvector there). That half was exercised
# once, by hand, at step 3 of the deployment, and by nothing since.
#
# So an engine is a `PERSISTENCE_BACKEND` value plus wherever its data goes, and
# the corpus is scored once PER ENGINE. Chantier 8 of `TODO_priorities.md`.

SQLITE = "sqlite"
POSTGRES = "postgres"

# The opt-in for the Postgres pass. Read from the environment DIRECTLY, and
# deliberately NOT a `Settings` field, for two reasons that both matter:
#
#   * `offline_settings` exists so that a score never depends on the developer's
#     `.env` (see its docstring). A field would put the scorer's own input back
#     on exactly that path.
#   * The application never connects to "the evaluation database". Reusing
#     `DATABASE_URL` would mean `make score` creating — and DROPPING — schemas in
#     whatever database a developer happens to have configured. A separate name
#     makes the Postgres pass something you ASK for.
EVAL_DATABASE_URL_ENV = "EVAL_DATABASE_URL"

# The schemas this harness owns, and may therefore destroy. The prefix IS the
# safety argument: `purge_eval_schemas` deletes by `LIKE '<prefix>%'`, so it can
# only ever reach namespaces it created itself. Two prefixes because the scorer
# and the test suite must not share a slot — `make check` and `make score` are
# separate processes and nothing orders them.
SCORE_SCHEMA_PREFIX = "eval_score_"
TEST_SCHEMA_PREFIX = "eval_test_"


def eval_postgres_url() -> str | None:
    """The Postgres to ALSO score against, or `None` to score SQLite alone."""
    return os.environ.get(EVAL_DATABASE_URL_ENV) or None


@dataclass(frozen=True)
class MemoryEngine:
    """One durable backend, handing out one isolated SLOT per corpus case.

    A slot is "the storage this case gets to itself": a pair of SQLite files, or
    a Postgres schema. Sharing storage between cases would let one case's facts
    satisfy another's recall — and R3, whose entire job is to prove that does NOT
    happen, would then pass by accident.

    Both factories (`get_store`, `get_checkpointer`) are the REAL ones: the
    backend switch, the index config and `setup()` are the code under test. Only
    the embeddings are faked, because the alternative is a network call and a
    credential.
    """

    backend: str
    workdir: Path
    database_url: str | None = None
    schema_prefix: str = SCORE_SCHEMA_PREFIX

    def schema(self, slot: str) -> str:
        """The Postgres schema backing one slot. Hyphens are not identifiers."""
        return f"{self.schema_prefix}{slot}".replace("-", "_").lower()

    def settings(self, slot: str) -> Settings:
        """Real `Settings` for one slot — the same object the application boots on.

        `memory_ttl_days=None` on both branches, and not for symmetry: with a TTL
        the Postgres store starts a background sweeper, and a thread deleting
        rows while the corpus is being scored would make the note depend on how
        long the run took.
        """
        if self.backend == SQLITE:
            return offline_settings(
                persistence_backend=SQLITE,
                working_memory_db_path=str(self.workdir / f"{slot}-fil.db"),
                agent_memory_db_path=str(self.workdir / f"{slot}-memories.db"),
                memory_ttl_days=None,
            )
        return offline_settings(
            persistence_backend=POSTGRES,
            database_url=self.database_url,
            database_schema=self.schema(slot),
            memory_ttl_days=None,
        )

    def store(self, slot: str) -> BaseStore:
        """The long-term store (R2/R3/R5) on this slot."""
        with _fake_embeddings():
            return get_store(self._record(slot))

    def checkpointer(self, slot: str) -> BaseCheckpointSaver:
        """The short-term checkpointer (R1) on this slot.

        Durable on both engines, unlike the in-memory saver this used to build.
        That is what lets R1 be read back through a SECOND saver object over the
        same storage (`resume_history`) instead of through the one that wrote it.
        """
        return get_checkpointer(self._record(slot))

    def _record(self, slot: str) -> Settings:
        """Settings for a slot, noting any pool the factories are about to open.

        The note is what `close_eval_pools` needs: `get_postgres_pool` is
        `lru_cache`d and a cache exposes no keys, so the only way to close what we
        opened is to have written it down on the way in.
        """
        settings = self.settings(slot)
        if self.backend == POSTGRES and self.database_url:
            _opened_postgres_pools.add((self.database_url, self.schema(slot)))
        return settings


def purge_eval_schemas(url: str, prefix: str) -> None:
    """RESET the schemas this harness owns, and make pgvector reachable from them.

    Two jobs. The order matters, and so does the fact that job 1 **re-creates**
    what it drops — both details were paid for in measured bugs.

    1. **Reset, not merely drop.** A score has to be reproducible. SQLite gets
       that for free (a fresh temporary directory per run); Postgres is a server
       that remembers, so a corpus that lost a case — or an embeddings model of a
       different width — would leave rows and a `vector(N)` column behind, and the
       next run would fail, or worse pass, for reasons unrelated to the code.

       The schema is then created again immediately, and that is what makes this
       function safe to call MORE THAN ONCE in a process. `get_postgres_pool` is
       `lru_cache`d: a second call for the same schema is a CACHE HIT, so
       `configure` never re-runs and nothing recreates the schema. A bare `DROP`
       therefore left every cached pool pointing at a schema that no longer
       existed — and Postgres **silently ignores** a missing entry in
       `search_path`, so the next `setup()` created its tables in `public`
       instead. Measured on 2026-07-30: two `run_eval()` calls in one process
       (exactly what `tests/test_mlops.py` does through its two fixtures)
       collapsed all eleven slots into `public`, and the corpus still scored
       24/24 — the per-case isolation was gone and nothing said so. The corpus
       passed only because `memories_namespace(user_id)` was carrying the
       isolation on its own, which is precisely the "passing for the wrong
       reason" this chantier exists to stop.

    2. **THEN make `vector` resolvable.** `CREATE EXTENSION IF NOT EXISTS` matches
       by NAME across the whole database, so it is a no-op when the extension
       already sits in someone else's schema — and it does **not** relocate it.
       Two ways that bites: `get_postgres_pool` creates the extension with
       `search_path` already pointing at its own schema (so an application that
       booted first parks pgvector in `DATABASE_SCHEMA`), or job 1 just dropped
       the schema that held it. Either way the eval schemas then die at `setup()`
       with `type "vector" does not exist` — measured, with `EVAL_DATABASE_URL`
       aimed at a database the app had already initialised.

       `public` is on the `search_path` of EVERY backend here
       (`postgres_conn._configure_connection`), so moving the extension there
       makes it resolvable for us *and* for the application — never less.
    """
    # Local import: hand-written DDL belongs to this path only — everything else
    # goes through the shared pool in `memory/postgres_conn.py`.
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True) as conn:
        # `pg_namespace`, not `information_schema.schemata`: the latter only shows
        # schemas the current role OWNS, so a run under a different user would see
        # an empty list and report "nothing stale" about schemas that are very much
        # still there. A cleanup that silently cleans nothing is the failure mode
        # to avoid here.
        #
        # And `left(nspname, n) = prefix`, NOT `LIKE prefix || '%'`: `_` is a
        # single-character WILDCARD in `LIKE`, and both prefixes end in one. The
        # pattern `eval_score_%` therefore also matched `eval_scores` and
        # `eval_scoreboard` — schemas this harness never created, dropped with
        # CASCADE. A prefix comparison is what the safety argument above actually
        # needs, so it is what the query does.
        stale = conn.execute(
            "SELECT nspname FROM pg_catalog.pg_namespace WHERE left(nspname, %s) = %s",
            (len(prefix), prefix),
        ).fetchall()
        for row in stale:
            name = sql.Identifier(row[0])
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(name))
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(name))

        installed = conn.execute(
            "SELECT n.nspname FROM pg_catalog.pg_extension e "
            "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'vector'"
        ).fetchone()
        if installed is None:
            conn.execute("CREATE EXTENSION vector SCHEMA public")
        elif installed[0] != "public":
            logger.info(
                "Relocating the pgvector extension from schema %s to public so the "
                "evaluation schemas can resolve the `vector` type",
                installed[0],
            )
            conn.execute("ALTER EXTENSION vector SET SCHEMA public")


def close_eval_pools() -> None:
    """Close every pool this harness opened, and forget the cache.

    `get_postgres_pool` is `lru_cache`d and closes nothing — right for an
    application process (one pool, held for its lifetime), wrong for a harness
    that opens one per corpus case. Without this, a pytest session holds a pool,
    and its background worker threads, for every case it scored: it grows with
    the corpus and would hit `too many clients` on a wider one.

    ⚠️ `lru_cache` cannot forget ONE entry, so this is all-or-nothing: it must
    only be called when nothing still holds a store or checkpointer it intends to
    use. Both call sites satisfy that — the end of `score_memory`, where the
    stores are local and discarded, and a pytest session teardown. Clearing the
    cache (rather than leaving closed pools in it) is the load-bearing half: a
    cached pool that has been closed would be handed to the next caller as if it
    were live.
    """
    if not _opened_postgres_pools:
        return
    for url, schema in sorted(_opened_postgres_pools):
        # A cache HIT — this hands back the existing pool rather than opening one
        # just to close it.
        get_postgres_pool(url, schema).close()
    _opened_postgres_pools.clear()
    get_postgres_pool.cache_clear()


def memory_engines(
    workdir: Path, *, database_url: str | None = None
) -> tuple[MemoryEngine, ...]:
    """The engines to score the memory corpus against, in order.

    SQLite always: it needs nothing but a temporary directory, so the corpus is
    never left unscored. Postgres only when a URL is offered — `make score` on a
    laptop must not require a database, and a laptop without one still gets its
    12 cases plus a report that SAYS Postgres was not exercised (`write_report`).
    That last part is the whole difference between a known gap and a blind spot;
    `--require-postgres` is how CI refuses the gap.

    A Postgres that is configured but unreachable makes the run FAIL, loudly, and
    is deliberately not turned into twelve failed cases: an infrastructure
    problem that reads as "the agent lost its memory" is the worse diagnosis.
    """
    engines = [MemoryEngine(SQLITE, workdir)]
    url = database_url or eval_postgres_url()
    if url:
        # Here and nowhere else: this is the one moment in a run that is
        # guaranteed to be before the first pool exists (see `purge_eval_schemas`).
        purge_eval_schemas(url, SCORE_SCHEMA_PREFIX)
        engines.append(MemoryEngine(POSTGRES, workdir, database_url=url))
    return tuple(engines)


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


def resume_history(checkpointer: BaseCheckpointSaver, config: dict) -> list[Any]:
    """Read a fil back through a graph that never wrote it.

    `get_state` on the graph that just ran would be satisfied by anything the
    saver happens to hold in memory. A brand-new graph, over a brand-new saver
    object on the same storage, is what "the fil survived the process" actually
    means — the same reasoning as R5's `gone_from_disk`.

    Returns `[]` rather than raising when nothing was persisted: that is a real
    R1 failure with a real detail line, not a crashed scorer.
    """

    def noop(state: MessagesState) -> dict:
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("noop", noop)
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    graph = builder.compile(checkpointer=checkpointer)
    return list(graph.get_state(config).values.get("messages", []))


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


def _check_r1(case: dict[str, Any], engine: MemoryEngine, slot: str) -> CaseResult:
    """R1: what the customer said early is still in the fil 30 turns later."""
    _, config, turns = replay_conversation(
        case, engine.checkpointer(slot), pad_to=R1_MIN_TURNS
    )
    # A second saver over the same storage: the fil must live in the ENGINE, not
    # in the object that wrote it.
    history = resume_history(engine.checkpointer(slot), config)

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


def _check_r2(case: dict[str, Any], engine: MemoryEngine, slot: str) -> CaseResult:
    """R2: a second session, days later, finds what the first one learned.

    Two independent store objects over the same slot — the second never sees the
    first, which is exactly the situation "the customer comes back next week"
    puts the agent in.
    """
    remember_user_turns(engine.store(slot), case)

    session2 = engine.store(slot)
    recalled = " ".join(
        record.text
        for record in search_user_memories(
            session2, case["user_id"], case["evaluation"]["question"], limit=10
        )
    )
    expected = case["evaluation"]["expected_substring"]
    passed = expected in recalled
    return CaseResult(case["id"], passed, "" if passed else f"{expected!r} not recalled")


def _check_r3(
    cases: list[dict[str, Any]], engine: MemoryEngine, slot: str
) -> list[CaseResult]:
    """R3, on the pair the corpus built for it.

    The two cases are the same sentence with a different order number, stored for
    two different customers. Under bag-of-words embeddings the two facts have
    IDENTICAL vectors, so similarity cannot tell them apart — the only thing
    keeping them separate is `memories_namespace(user_id)`. The pair therefore
    shares ONE slot on purpose: isolation has nothing to prove across two
    databases.
    """
    store = engine.store(slot)
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


def _check_r5(case: dict[str, Any], engine: MemoryEngine, slot: str) -> CaseResult:
    """R5: "oublie mon adresse" deletes it, and the deletion is checkable.

    Four ways of looking, because a deletion that only holds for one of them is
    not a deletion: the return value that says what was destroyed, the audit dump
    an operator uses, the semantic recall the agent uses, and — on a SECOND store
    over the same slot — the storage itself. An in-process cache that merely
    stopped returning the row would satisfy the first three and hand the fact back
    after a restart.
    """
    store = engine.store(slot)
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
        for record in list_user_memories(engine.store(slot), user_id)
    )
    passed = gone_from_dump and gone_from_recall and gone_from_disk
    return CaseResult(
        case["id"],
        passed,
        "" if passed else f"{forbidden!r} survived the erasure",
    )


MEMORY_TAGS = ("R1", "R2", "R3", "R5")


def _score_one_engine(engine: MemoryEngine) -> list[CaseResult]:
    """Run the whole 12-case corpus against ONE engine.

    Each tag is scored against the mechanism that actually implements it — the
    one adaptation this port makes, and the same one `test_memory_cases.py`
    documents:

        R1  ->  the CHECKPOINTER, keyed by thread_id  (nothing dropped, no search)
        R2  ->  the STORE,        keyed by user_id    (durable, survives a restart)
        R3  ->  `memories_namespace`                  (one user cannot reach another)
        R5  ->  `forget_user_memories`                (deleted, and verified deleted)

    One slot per case (`r1-0`, `r2-1`, …), the R3 pair excepted: sharing storage
    across cases would let one case's facts satisfy another's recall.
    """
    results: list[CaseResult] = []
    for index, case in enumerate(load_memory_cases("R1")):
        results.append(_check_r1(case, engine, f"r1-{index}"))
    for index, case in enumerate(load_memory_cases("R2")):
        results.append(_check_r2(case, engine, f"r2-{index}"))
    results.extend(_check_r3(load_memory_cases("R3"), engine, "r3"))
    for index, case in enumerate(load_memory_cases("R5")):
        results.append(_check_r5(case, engine, f"r5-{index}"))
    return results


def score_memory(
    workdir: Path, *, engines: Sequence[MemoryEngine] | None = None
) -> CorpusRun:
    """Score the 12-case memory corpus, ONCE PER ENGINE. Deterministic, offline.

    The corpus is the same on every engine; what changes underneath is the thing
    that actually stores and searches the memories. So the dimension counts
    `12 × len(engines)` cases, and that is the honest total rather than an
    inflation: on Postgres these twelve cases exercise pgvector's similarity
    query and `PostgresSaver`'s round trip, neither of which SQLite can vouch for.
    See `memory_engines` for when the second pass happens.

    Two signals exist purely so the number cannot be read as more than it is —
    `engines`, and one `<backend>_rate` per engine. `write_report` turns a missing
    `postgres_rate` into a warning printed next to the note, because a memory
    score that silently means "on SQLite only" is the defect this chantier closes,
    not a defect it is allowed to reproduce.
    """
    engines = tuple(engines) if engines is not None else memory_engines(workdir)

    results: list[CaseResult] = []
    per_engine: dict[str, float] = {}
    try:
        for engine in engines:
            rows = [
                # The engine is part of a case's IDENTITY, not a detail of it:
                # without the suffix the report would list `R3-01` twice and never
                # say which store leaked.
                replace(row, case_id=f"{row.case_id}@{engine.backend}")
                for row in _score_one_engine(engine)
            ]
            per_engine[f"{engine.backend}_rate"] = (
                sum(1 for row in rows if row.passed) / len(rows) if rows else 0.0
            )
            results.extend(rows)
    finally:
        # One pool per slot is fine for the length of a scoring run and wrong to
        # keep afterwards — `run_eval` is called twice in a single pytest process,
        # and a caller may score repeatedly. In a `finally` so a raising engine
        # (an unreachable database) does not leak its connections either.
        close_eval_pools()

    by_tag = {
        tag: {
            f"{row['id']}@{engine.backend}"
            for engine in engines
            for row in load_memory_cases(tag)
        }
        for tag in MEMORY_TAGS
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
    signals["engines"] = float(len(engines))
    signals.update(per_engine)
    return CorpusRun(results=tuple(results), signals=signals)
