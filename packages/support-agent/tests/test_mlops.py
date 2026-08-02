"""The MLOps layer, run as its own acceptance suite.

Three criteria, kept in order and by name:

    test_scores_produced_and_versioned   -> four notes, versioned
    test_regression_blocks_delivery      -> a regression drops the note AND blocks
    test_report_contains_signals         -> the five signals are visible

Three adaptations, each argued where it happens: the DEGRADED agent is the real
`GUARDRAILS_ENABLED` kill switch rather than a hand-written stub; `quality` is `None` in
an offline run instead of a number; and the report is asserted through `fold()` because
it is written in accented French.

Then five tests where the actual design is defended: the hard floor must block what the
global average hides, the baseline must catch a slow decay, a stale baseline must NOT be
compared, and both deterministic dimensions must be PERFECT — which is what stops the
scorer from drifting away from what `test_moderation.py` and `test_memory_cases.py` mean
by "correct".

Offline by construction: no provider, no key, no network.
"""

from __future__ import annotations

import json

import pytest

from support_agent.eval.corpus import corpus_fingerprint, load_guardrail_cases, load_memory_cases
from support_agent.eval.mlops import (
    GUARDRAILS,
    HARD_FLOORS,
    MEMORY,
    QUALITY,
    Baseline,
    DeliveryBlocked,
    Dimension,
    Scores,
    current_version,
    enforce_threshold,
    load_baseline,
    run_eval,
    save_baseline,
    write_report,
)
from support_agent.eval.offline import POSTGRES, SQLITE
from support_agent.guardrails.moderation import fold


@pytest.fixture(scope="module")
def scores() -> Scores:
    """The healthy agent, scored offline. Module-scoped: it replays 12 corpora."""
    return run_eval()


@pytest.fixture(scope="module")
def degraded() -> Scores:
    """The DEGRADED agent: guardrails off.

    Not a test double. `GUARDRAILS_ENABLED=false` is a documented production
    switch whose meaning is "the graph is wired exactly as before, no guard node
    at all" — so this regression test measures a state that can really happen,
    which a hand-written `AllowAllGuardrails` stub could not.
    """
    return run_eval(guardrails_enabled=False)


# --- The three headline criteria --------------------------------------------


def test_scores_produced_and_versioned(scores: Scores) -> None:
    """Criterion: a global note plus per-dimension notes, versioned.

    ADAPTATION: a literal reading asserts `scores.quality is not None`. Here an OFFLINE
    run cannot measure quality — that needs a real LLM answering real questions —
    and returning a number anyway would mean inventing one. So `quality` is
    `None`, which is not the same thing as zero: `global_` averages the MEASURED
    dimensions only, so an offline run is not punished for what it did not do.
    The live path (`--live`) fills it in.
    """
    assert scores.global_ is not None and 0.0 <= scores.global_ <= 1.0
    assert scores.memory is not None
    assert scores.guardrails is not None
    assert scores.quality is None
    assert current_version()

    assert scores.global_ == pytest.approx((scores.memory + scores.guardrails) / 2)


def test_regression_blocks_delivery(scores: Scores, degraded: Scores) -> None:
    """Criterion: a regression makes the note fall AND stops the delivery."""
    assert degraded.global_ < scores.global_

    enforce_threshold(scores, 0.8)

    with pytest.raises(DeliveryBlocked) as blocked:
        enforce_threshold(degraded, 0.8)

    message = str(blocked.value)
    assert GUARDRAILS in message
    assert "hate-1" in message


def test_report_contains_signals(scores: Scores, tmp_path) -> None:
    """Criterion: memory note, blocking rate, false positives, latency, cost.

    The report stays in proper French ("mémoire", accented) and the assertion goes
    through `fold()`, rather than degrading the deliverable to satisfy a `.lower()`
    comparison that does not strip accents.
    """
    report = tmp_path / "report.md"
    write_report(scores, report)

    folded = fold(report.read_text(encoding="utf-8"))
    for signal in ("memoire", "blocage", "faux positif", "latence", "cout"):
        assert signal in folded, f"signal absent du rapport : {signal}"


# --- Where the design itself is defended -------------------------------------


def test_the_deterministic_dimensions_are_perfect(scores: Scores) -> None:
    """The anti-drift guard, and the reason the scorer can be trusted at all.

    `eval/offline.py` COUNTS what `test_moderation.py` and `test_memory_cases.py`
    ASSERT. They share the plumbing, but the predicates are written separately, so a
    predicate could quietly stop meaning what the assertions mean — and a scorer that
    measures the wrong thing is worse than no scorer.

    The memory total is `12 × engines`, not 12, and it is asserted against the engines
    the run REPORTS having used: hard-coding 24 would fail on a laptop without Postgres,
    and hard-coding 12 would stop noticing whether the second pass ran at all.
    """
    memory = scores.dimensions[MEMORY]
    engines = int(memory.signals["engines"])
    assert engines >= 1
    assert memory.passed == memory.total == len(load_memory_cases()) * engines
    assert scores.dimensions[GUARDRAILS].passed == len(load_guardrail_cases()) == 35
    assert scores.memory == 1.0
    assert scores.guardrails == 1.0

    assert {SQLITE: 1.0} | ({POSTGRES: 1.0} if engines > 1 else {}) == {
        backend: rate
        for backend in (SQLITE, POSTGRES)
        if (rate := memory.signals.get(f"{backend}_rate")) is not None
    }


def test_the_hard_floor_blocks_what_the_global_average_hides() -> None:
    """THE property a single global threshold cannot express.

    One guardrail case regressing out of 35 — say a jailbreak that now walks through —
    moves the global note to 0.986, a comfortable pass against 0.8. It must still block,
    because a deterministic safety corpus that is not perfect is not "nearly right".
    """
    leaky = Scores(
        version="test",
        corpus=corpus_fingerprint(),
        dimensions={
            MEMORY: Dimension(MEMORY, passed=12, total=12),
            GUARDRAILS: Dimension(GUARDRAILS, passed=34, total=35),
        },
    )
    assert leaky.global_ > 0.8
    with pytest.raises(DeliveryBlocked, match=GUARDRAILS):
        enforce_threshold(leaky, 0.8)


def test_a_memory_note_scored_on_sqlite_alone_is_refused_when_postgres_is_required() -> None:
    """The gate no number can express: WHICH ENGINE was measured.

    A perfect 12/12 on SQLite passes the threshold, the hard floor and the baseline, all
    three, while saying nothing about pgvector — the store that will actually hold
    customer data, and therefore the one R3 has to be proven on.

    So CI runs `--require-postgres`, and this asserts what that buys: a database service
    that failed to start, or a typo in `EVAL_DATABASE_URL`, turns into a red run instead
    of a green one covering half as much.
    """
    sqlite_only = Scores(
        version="test",
        corpus=corpus_fingerprint(),
        dimensions={
            MEMORY: Dimension(
                MEMORY, passed=12, total=12, signals={"engines": 1.0, f"{SQLITE}_rate": 1.0}
            ),
            GUARDRAILS: Dimension(GUARDRAILS, passed=35, total=35),
        },
    )
    enforce_threshold(sqlite_only, 0.8)

    with pytest.raises(DeliveryBlocked, match="Postgres"):
        enforce_threshold(sqlite_only, 0.8, require_postgres=True)

    both = Scores(
        version="test",
        corpus=corpus_fingerprint(),
        dimensions={
            MEMORY: Dimension(
                MEMORY,
                passed=24,
                total=24,
                signals={"engines": 2.0, f"{SQLITE}_rate": 1.0, f"{POSTGRES}_rate": 1.0},
            ),
            GUARDRAILS: Dimension(GUARDRAILS, passed=35, total=35),
        },
    )
    enforce_threshold(both, 0.8, require_postgres=True)


def test_the_report_says_which_engines_the_memory_note_covers(
    scores: Scores, tmp_path
) -> None:
    """The other half of the coverage gate: an honest number, not just a gated one.

    `make score` on a laptop legitimately scores SQLite alone. What must never
    happen again is that run printing "mémoire 12/12" with no mention of the
    engine — which is exactly how the blind spot survived for so long. So
    the report names the engines it measured, and warns when Postgres is missing.
    """
    report = tmp_path / "report.md"
    write_report(scores, report)
    text = report.read_text(encoding="utf-8")

    assert SQLITE in text
    if int(scores.dimensions[MEMORY].signals["engines"]) > 1:
        assert POSTGRES in text
    else:
        assert "⚠️" in text and "production" in text


def test_a_slow_decay_is_caught_by_the_baseline_not_by_the_threshold() -> None:
    """Non-regression is RELATIVE, and a fixed threshold cannot express it.

    Quality has no absolute floor (it is not deterministic), so a drift from 7/7
    to 5/7 clears any sane global threshold. Only a comparison against an accepted
    level catches it. And the tolerance is expressed in CASES: one lost case is a
    coin toss on a corpus with two measured-unstable expectations, two is a signal.
    """
    baseline = Baseline(
        version="accepted",
        corpus=corpus_fingerprint(),
        dimensions={QUALITY: {"score": 1.0, "passed": 7, "total": 7}},
    )

    def quality_scores(passed: int) -> Scores:
        return Scores(
            version="candidate",
            corpus=corpus_fingerprint(),
            dimensions={QUALITY: Dimension(QUALITY, passed=passed, total=7)},
        )

    enforce_threshold(quality_scores(6), 0.8, baseline=baseline)

    with pytest.raises(DeliveryBlocked, match="régresse de 2 cas"):
        enforce_threshold(quality_scores(5), 0.8, baseline=baseline)


def test_the_baseline_compares_per_engine_not_by_raw_case_count() -> None:
    """A raw count stopped being comparable once the corpus replays per engine.

    Both directions were reachable and both are wrong: a baseline recorded with Postgres
    (24/24) against a perfect 12/12 laptop run read as a 12-case regression, and the
    reverse made the loss negative, silently retiring the gate.

    Neither is a regression — it is the same corpus on a different number of engines. So
    the comparison is per engine on both sides.
    """
    def memory_scores(passed: int, total: int, engines: int) -> Scores:
        return Scores(
            version="candidate",
            corpus=corpus_fingerprint(),
            dimensions={
                MEMORY: Dimension(
                    MEMORY, passed=passed, total=total, signals={"engines": float(engines)}
                )
            },
        )

    two_engines = Baseline(
        version="accepted",
        corpus=corpus_fingerprint(),
        dimensions={MEMORY: {"score": 1.0, "passed": 24, "total": 24, "engines": 2}},
    )
    enforce_threshold(memory_scores(12, 12, 1), 0.8, baseline=two_engines)

    one_engine = Baseline(
        version="accepted",
        corpus=corpus_fingerprint(),
        dimensions={MEMORY: {"score": 1.0, "passed": 12, "total": 12, "engines": 1}},
    )
    enforce_threshold(memory_scores(24, 24, 2), 0.8, baseline=one_engine)

    with pytest.raises(DeliveryBlocked, match="régresse de 2 cas"):
        enforce_threshold(memory_scores(20, 24, 2), 0.8, baseline=one_engine)


def test_a_baseline_recorded_on_another_corpus_is_not_compared() -> None:
    """The comparison people get wrong: two notes over different questions.

    A baseline whose corpus fingerprint differs was recorded against other cases.
    Comparing anyway would manufacture a regression or hide one, so the
    comparison is SKIPPED — while the hard floors keep applying, and the report
    states why (`baseline_status`). A silently skipped gate is a gate you only
    think you have.
    """
    stale = Baseline(
        version="old",
        corpus="0000deadbeef",
        dimensions={QUALITY: {"score": 1.0, "passed": 7, "total": 7}},
    )
    collapsed = Scores(
        version="candidate",
        corpus=corpus_fingerprint(),
        dimensions={QUALITY: Dimension(QUALITY, passed=0, total=7)},
    )
    with pytest.raises(DeliveryBlocked) as blocked:
        enforce_threshold(collapsed, 0.8, baseline=stale)
    assert "régresse" not in str(blocked.value)
    assert "note globale" in str(blocked.value)


def test_a_missing_baseline_blocks_nothing_by_itself(scores: Scores, tmp_path) -> None:
    """A fresh clone has no accepted level, and that must not be an error."""
    assert load_baseline(tmp_path / "absent.json") is None
    enforce_threshold(scores, 0.8, baseline=None)


def test_the_baseline_round_trips(scores: Scores, tmp_path) -> None:
    """What is written is what is read back, and it carries the corpus identity."""
    path = save_baseline(scores, tmp_path / "eval-baseline.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["corpus"] == corpus_fingerprint()

    reloaded = load_baseline(path)
    assert reloaded is not None
    assert reloaded.corpus == scores.corpus
    assert reloaded.passed(MEMORY) == scores.dimensions[MEMORY].passed
    assert reloaded.score(GUARDRAILS) == pytest.approx(scores.guardrails)


def test_current_version_names_the_code_the_model_and_the_corpus() -> None:
    """A note is only comparable if all three are the same. So all three are in it."""
    version = current_version()
    assert corpus_fingerprint() in version
    assert "/" in version.split("+")[1]


def test_the_deterministic_dimensions_are_the_ones_with_a_floor() -> None:
    """The floors apply to what has no run-to-run variance — and only to that.

    Pinned rather than left to reading: giving `quality` a hard floor would import
    the documented flakiness of `honest_refusal` into the delivery gate, which is
    the exact failure known as "bloquer pour du bruit".
    """
    assert set(HARD_FLOORS) == {MEMORY, GUARDRAILS}
    assert QUALITY not in HARD_FLOORS
    assert set(HARD_FLOORS.values()) == {1.0}
