"""Tests for the HTTP door (`server.py`) — transport, not agent behaviour.

The agent's thinking is tested elsewhere. Here we assert only what the web layer owes
its callers:

    - it refuses to boot wide open (fail-closed authentication);
    - it rejects a caller without the service key;
    - it emits well-formed SSE, terminated by a `done` event;
    - an error travels INSIDE the stream, because the status code is long gone.

The seam is faked throughout: no graph is built, so these run offline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from support_agent import server
from support_agent.config import Settings, get_settings

API_KEY = "test-key"


def _sse_events(body: str) -> list[dict]:
    """Parse an SSE body into the list of decoded event payloads."""
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _use_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    """Force the settings the server sees, ignoring the developer's `.env`.

    Environment variables are not enough: `config.py` calls `load_dotenv()` at import
    time, so `monkeypatch.delenv("API_KEY")` does not make the key absent. Passing the
    fields as constructor kwargs wins over both `.env` and the environment, which is the
    only way to assert "no key configured" for real.

    Two injection points are needed because the server reads settings two ways: the
    lifespan calls `get_settings()` directly, while the routes receive it through
    `Depends`, which resolves the ORIGINAL function object.
    """
    settings = Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    server.app.dependency_overrides[get_settings] = lambda: settings


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Default for every test: authentication ON, with a known key."""
    _use_settings(monkeypatch, api_key=API_KEY, api_allow_unauthenticated=False)
    yield
    server.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fake_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the agent: never build a real graph in a transport test."""

    async def _fake_stream_reply(
        message: str, *, user_id: str, thread_id: str
    ) -> AsyncIterator[str]:
        yield f"Réponse à « {message} » pour {user_id}."

    monkeypatch.setattr(server, "stream_reply", _fake_stream_reply)
    monkeypatch.setattr(server, "get_agent", lambda: object())


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose context manager RUNS the lifespan (warm-up + auth check)."""
    with TestClient(server.app) as test_client:
        yield test_client


# --- Fail-closed startup -----------------------------------------------------


def test_refuses_to_start_without_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key and no explicit opt-out => the process must not come up.

    The accident this prevents: deploying with the secret forgotten and serving a
    paid LLM to the whole internet, with nothing anywhere saying so.
    """
    _use_settings(monkeypatch, api_key=None, api_allow_unauthenticated=False)

    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(server.app):
            pass


def test_starts_unauthenticated_when_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insecure mode exists for local runs — but only when asked for by name."""
    _use_settings(monkeypatch, api_key=None, api_allow_unauthenticated=True)

    with TestClient(server.app) as client:
        response = client.post("/chat", json={"message": "Bonjour"})
        assert response.status_code == 200


# --- Probes ------------------------------------------------------------------


def test_health_needs_no_key_and_touches_nothing(client: TestClient) -> None:
    """Liveness must stay dependency-free, or a warm-up gets restarted in a loop."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_the_warmed_up_agent(client: TestClient) -> None:
    """Readiness flips to true only once the lifespan has built the agent."""
    assert client.get("/ready").json() == {"ready": True}


# --- Authentication ----------------------------------------------------------


def test_chat_without_key_is_rejected(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "Bonjour"})
    assert response.status_code == 401


def test_chat_with_wrong_key_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/chat", json={"message": "Bonjour"}, headers={"X-API-Key": "nope"}
    )
    assert response.status_code == 401


def test_chat_with_a_non_ascii_key_is_rejected_not_crashed() -> None:
    """A wrong key must always be a 401, whatever bytes it is made of.

    Starlette decodes headers as latin-1, and `secrets.compare_digest` REFUSES a
    `str` with non-ASCII characters: a single byte >= 0x80 used to raise inside
    the dependency and surface as a 500 — an unauthenticated caller could produce
    a stack trace on demand. The header must be sent as BYTES: httpx rejects the
    `str` form, which is exactly why this case had never been exercised.
    """
    with TestClient(server.app, raise_server_exceptions=False) as raw_client:
        response = raw_client.post(
            "/chat",
            json={"message": "Bonjour"},
            headers={"X-API-Key": "clé".encode()},
        )
    assert response.status_code == 401


def test_a_legitimate_non_ascii_key_still_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix must not degrade into "reject anything non-ASCII"."""
    _use_settings(monkeypatch, api_key="clé-du-service", api_allow_unauthenticated=False)

    with TestClient(server.app) as raw_client:
        response = raw_client.post(
            "/chat",
            json={"message": "Bonjour"},
            headers={"X-API-Key": "clé-du-service".encode()},
        )
    assert response.status_code == 200


def test_empty_message_is_rejected_before_the_agent(client: TestClient) -> None:
    """Validation belongs at the door: an empty message must not cost an LLM call."""
    response = client.post(
        "/chat", json={"message": ""}, headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422


# --- The stream itself -------------------------------------------------------


def test_chat_streams_sse_and_terminates(client: TestClient) -> None:
    """A well-formed exchange: SSE content type, a chunk, then `done`."""
    response = client.post(
        "/chat",
        json={"message": "Où est ma commande ?", "thread_id": "t-1", "user_id": "u-1"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response.text)
    assert [event["type"] for event in events] == ["chunk", "done"]
    assert "u-1" in events[0]["text"]


def test_response_echoes_the_keys_actually_used(client: TestClient) -> None:
    """A caller that sent no thread_id still needs to know which one it got.

    Without this it cannot continue the conversation: its next request would mint
    yet another thread, and the agent would look like it has no memory at all.
    """
    response = client.post(
        "/chat", json={"message": "Bonjour"}, headers={"X-API-Key": API_KEY}
    )

    assert response.headers["X-Thread-Id"]
    assert response.headers["X-User-Id"] == server.DEMO_USER_ID


def test_seam_failure_is_reported_inside_the_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once streaming starts the status code is spent: errors must be events.

    The response still says 200 — that is not a bug, it is how SSE works, and it
    is why the contract makes `type: "error"` meaningful to clients.
    """

    async def _boom(message: str, *, user_id: str, thread_id: str) -> AsyncIterator[str]:
        raise RuntimeError("seam itself is broken")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(server, "stream_reply", _boom)

    response = client.post(
        "/chat", json={"message": "Bonjour"}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event["type"] for event in events] == ["error", "done"]
