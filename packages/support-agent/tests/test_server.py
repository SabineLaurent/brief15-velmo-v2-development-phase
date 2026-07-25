"""Tests for the HTTP door (`server.py`) — transport, not agent behaviour.

The agent's thinking is tested elsewhere (`test_api_seam.py` for the seam's
contract, `test_guardrails.py`, `test_robustness.py`). Here we assert only what
the web layer owes its callers:

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
from support_agent.config import get_settings

API_KEY = "test-key"


def _sse_events(body: str) -> list[dict]:
    """Parse an SSE body into the list of decoded event payloads."""
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the auth settings for every test, whatever the developer's `.env` says.

    `get_settings` is `lru_cache`d, so the cache must be cleared on BOTH sides:
    entering (to drop whatever a previous test or the real `.env` produced) and
    leaving (so a cached test value never leaks into another test).
    """
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("API_ALLOW_UNAUTHENTICATED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_ALLOW_UNAUTHENTICATED", "false")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(server.app):
            pass


def test_starts_unauthenticated_when_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insecure mode exists for local runs — but only when asked for by name."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_ALLOW_UNAUTHENTICATED", "true")
    get_settings.cache_clear()

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
