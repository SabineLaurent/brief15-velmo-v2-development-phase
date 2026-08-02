"""Tests for the persistence backend switch (`PERSISTENCE_BACKEND`).

The whole promise of `memory/` is that changing where state lives is a `.env` change,
not a code change — and the `postgres` branch had raised `NotImplementedError` since it
was written, so the branch that had never run is exactly the one that needs assertions.

Pure UNIT tests: no database is started. What is checked is the DECISION (which backend,
with which config, failing how), not the storage engine, which is LangGraph's code. A
test suite that needs a Postgres to run is a test suite that stops being run.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from support_agent.config import Settings
from support_agent.memory.long_term import _ttl_config, get_store
from support_agent.memory.postgres_conn import require_database_url
from support_agent.memory.short_term import get_checkpointer


def _settings(**overrides: object) -> Settings:
    """Build Settings from explicit values, ignoring the developer's own .env.

    Constructor kwargs beat both `.env` and the environment — the same trick as
    `test_server.py`, and for the same reason: `config.py` calls `load_dotenv()`
    at import, so the host's `.env` is already inside `os.environ` and no amount
    of `monkeypatch.delenv` can make a variable absent.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# --- The switch itself --------------------------------------------------------


def test_memory_backend_is_the_declared_default() -> None:
    """Zero configuration must not silently write files or open sockets.

    Asserted on the FIELD's default rather than on an instance: an instance
    still reads `os.environ`, which the developer's `.env` has already
    populated, so `Settings().persistence_backend` measures the machine, not the
    code. This is the one property that constructor kwargs cannot check.
    """
    assert Settings.model_fields["persistence_backend"].default == "memory"
    assert isinstance(get_checkpointer(_settings(persistence_backend="memory")), InMemorySaver)


def test_unknown_backend_is_rejected_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must fail loudly at boot, not fall back to a surprise default.

    Asserted on BOTH factories on purpose: one variable drives them, so a name
    that one accepts and the other refuses would mean a half-configured process.
    """
    _forbid_embeddings(monkeypatch)
    settings = _settings(persistence_backend="postgress")

    with pytest.raises(ValueError, match="postgress"):
        get_checkpointer(settings)
    with pytest.raises(ValueError, match="postgress"):
        get_store(settings)


def test_backend_name_is_case_insensitive() -> None:
    """`PERSISTENCE_BACKEND=Memory` in a .env is a person, not a bug."""
    assert isinstance(get_checkpointer(_settings(persistence_backend="MEMORY")), InMemorySaver)


# --- The Postgres branch: failing well ----------------------------------------


def test_postgres_without_database_url_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actionable error is the feature.

    A missing connection string discovered inside a customer request is the same bug
    found at the worst moment. Both factories must refuse at boot, and the message must
    name the variable AND show a valid value.

    `_forbid_embeddings` is what makes the test's own name true: without it, `get_store`
    probed the embeddings model FIRST, so the check was only reachable from a machine
    that already had a provider key.
    """
    _forbid_embeddings(monkeypatch)
    settings = _settings(persistence_backend="postgres", database_url=None)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        get_checkpointer(settings)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        get_store(settings)


def test_require_database_url_rejects_empty_string() -> None:
    """`DATABASE_URL=` in a .env is "unset", not "connect to the empty host"."""
    for missing in (None, ""):
        with pytest.raises(ValueError, match="DATABASE_URL"):
            require_database_url(missing)

    url = "postgres://agent:agent@postgres:5432/agent"
    assert require_database_url(url) == url


# --- Retention (GDPR) ---------------------------------------------------------


def test_ttl_is_converted_from_days_to_minutes() -> None:
    """LangGraph counts TTLs in MINUTES; we configure retention in days.

    Getting this wrong is silent and expensive in both directions: a 1440x too
    short TTL deletes customer memories a day later, a 1440x too long one keeps
    personal data for centuries. Hence an assertion on the arithmetic itself.
    """
    config = _ttl_config(_settings(memory_ttl_days=365))
    assert config is not None
    assert config["default_ttl"] == 365 * 24 * 60
    assert config["sweep_interval_minutes"] == 60


def test_ttl_can_be_disabled() -> None:
    """No retention configured = keep forever, the sqlite/in-memory behaviour."""
    assert _ttl_config(_settings(memory_ttl_days=None)) is None


def test_memory_store_ignores_ttl_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TTL sweeper is a Postgres feature; the other backends must not break.

    Embeddings are faked: `get_store` probes the model for its vector size, and
    a unit test must not need a provider key or a network call for that.
    """
    monkeypatch.setattr(
        "support_agent.memory.long_term.get_embeddings",
        lambda settings: _FakeEmbeddings(),
    )
    store = get_store(_settings(persistence_backend="memory", memory_ttl_days=30))
    assert isinstance(store, InMemoryStore)


class _FakeEmbeddings:
    """Minimal stand-in: `get_store` only calls `embed_query` to learn `dims`."""

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 1.0, 0.0]


def _forbid_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn any embeddings call into an immediate, unmistakable failure.

    This is the tool that upgrades "it raised the right error" into "it raised
    it WITHOUT doing I/O" — an ordering no assertion on the exception can state
    on its own. `AssertionError` is deliberately not a `ValueError`: it escapes
    the surrounding `pytest.raises` instead of being mistaken for the expected
    failure, so a regression reads as "config validated too late", not as a pass.
    """

    def _explode(settings: Settings) -> None:
        raise AssertionError("config must be validated before any embeddings call")

    monkeypatch.setattr("support_agent.memory.long_term.get_embeddings", _explode)
