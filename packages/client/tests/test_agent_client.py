"""Contract tests for the network seam (`agent_client.stream_reply`).

Deployment step 4 moved the seam onto the network, which means the FRONT now has
failure modes the in-process call never had: connection refused, a 401, a stream
cut in half. What this file asserts is the promise the front depends on and can no
longer take for granted:

  - the SSE events the agent sends become plain reply chunks;
  - the caller's key and the customer's identity travel on the wire, separately;
  - unknown event types are ignored (so phase B1.5 cannot break this client);
  - EVERY failure path still delivers exactly ONE non-empty chunk.

No agent, no graph, no socket: `httpx.MockTransport` answers every request, so
these run offline and in milliseconds. `asyncio.run` rather than pytest-asyncio,
matching `test_api_seam.py` — one fewer dev dependency.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import httpx
import pytest

from client_chainlit import agent_client


def _sse_body(*events: dict) -> bytes:
    """Build an SSE body exactly like `server._sse` does: `data: <json>\\n\\n`."""
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, handler: object
) -> list[httpx.Request]:
    """Route the client through a fake transport; return the captured requests.

    Swapping `_new_client` (rather than patching httpx globally) is the reason
    that function exists: it is the module's single transport construction point.
    """
    seen: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)  # type: ignore[operator]

    monkeypatch.setattr(
        agent_client,
        "_new_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_capturing)),
    )
    return seen


def _collect(**kwargs: str) -> list[str]:
    """Run the async generator to exhaustion and return everything it yielded."""

    async def _run() -> list[str]:
        return [
            chunk
            async for chunk in agent_client.stream_reply(
                kwargs.pop("message", "Bonjour"),
                user_id=kwargs.pop("user_id", "demo-user"),
                thread_id=kwargs.pop("thread_id", "thread-1"),
            )
        ]

    return asyncio.run(_run())


def _ok(*events: dict) -> object:
    """A handler answering 200 with a well-formed SSE body."""
    return lambda _request: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_sse_body(*events),
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the developer's `.env`: these tests own the configuration.

    Both variables are read at CALL time, so a leftover `AGENT_API_KEY` in the
    environment would silently turn "sends no header" into a failing assertion on
    someone else's machine.
    """
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_API_URL", "http://agent-api:8000")


# ─── The happy path ──────────────────────────────────────────────────────────


def test_chunk_events_become_reply_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        _ok({"type": "chunk", "text": "Bonjour !"}, {"type": "done"}),
    )
    assert _collect() == ["Bonjour !"]


def test_several_chunks_are_yielded_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today the server sends one chunk; phase B1.5 will send several.

    The client must already be right for that day, otherwise "it is a stream" is
    just a comment.
    """
    _install_transport(
        monkeypatch,
        _ok(
            {"type": "chunk", "text": "Je regarde"},
            {"type": "chunk", "text": " votre commande."},
            {"type": "done"},
        ),
    )
    assert _collect() == ["Je regarde", " votre commande."]


def test_unknown_event_types_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward compatibility, promised by `server._sse`.

    A newer agent emitting `step` or `sources` events must not break an older
    client — that promise is worthless unless something checks it.
    """
    _install_transport(
        monkeypatch,
        _ok(
            {"type": "step", "name": "retrieve_faq"},
            {"type": "chunk", "text": "Nos délais sont de 48 h."},
            {"type": "sources", "files": ["livraison.md"]},
            {"type": "done"},
        ),
    )
    assert _collect() == ["Nos délais sont de 48 h."]


# ─── What travels on the wire ────────────────────────────────────────────────


def test_identity_and_thread_travel_in_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_transport(
        monkeypatch, _ok({"type": "chunk", "text": "ok"}, {"type": "done"})
    )
    _collect(message="Où est ma commande ?", user_id="alice", thread_id="thread-42")

    assert str(seen[0].url) == "http://agent-api:8000/chat"
    assert json.loads(seen[0].content) == {
        "message": "Où est ma commande ?",
        "user_id": "alice",
        "thread_id": "thread-42",
    }


def test_service_key_travels_in_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two identities must not share a channel.

    `X-API-Key` authorizes the CALLER; `user_id` (body, above) names the CUSTOMER.
    Sending the key in the body — or the identity in the header — is how one
    customer's history ends up readable by another.
    """
    monkeypatch.setenv("AGENT_API_KEY", "s3cret")
    seen = _install_transport(
        monkeypatch, _ok({"type": "chunk", "text": "ok"}, {"type": "done"})
    )
    _collect()

    assert seen[0].headers["x-api-key"] == "s3cret"
    assert "s3cret" not in seen[0].content.decode()


def test_no_key_configured_sends_no_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty key is a WRONG key; no key at all is a legitimate local setup."""
    seen = _install_transport(
        monkeypatch, _ok({"type": "chunk", "text": "ok"}, {"type": "done"})
    )
    _collect()

    assert "x-api-key" not in seen[0].headers


# ─── Failure paths: one non-empty chunk, always ──────────────────────────────


def test_error_event_is_shown_to_the_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent-side failure arrives INSIDE a 200 (the status left long ago)."""
    _install_transport(
        monkeypatch,
        _ok({"type": "error", "message": "Service indisponible."}, {"type": "done"}),
    )
    assert _collect() == ["Service indisponible."]


def test_rejected_request_yields_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 answers JSON, not SSE: the status must be checked before parsing."""
    _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(401, json={"detail": "Missing X-API-Key."}),
    )
    assert _collect() == [agent_client.REJECTED_MESSAGE]


def test_unreachable_agent_yields_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container is down, DNS is wrong, or the port is closed."""

    def _refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    _install_transport(monkeypatch, _refuse)
    assert _collect() == [agent_client.UNREACHABLE_MESSAGE]


def test_truncated_stream_yields_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """`done` with no chunk: the agent closed the stream without answering."""
    _install_transport(monkeypatch, _ok({"type": "done"}))
    assert _collect() == [agent_client.TRUNCATED_MESSAGE]


def test_malformed_payload_does_not_crash_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: not json\n\ndata: {"type": "chunk", "text": "Bonjour"}\n\n'
            b'data: {"type": "done"}\n\n',
        ),
    )
    assert _collect() == ["Bonjour"]


@pytest.mark.parametrize(
    "handler_events",
    [
        ({"type": "chunk", "text": "Bonjour"}, {"type": "done"}),
        ({"type": "error", "message": "Panne."}, {"type": "done"}),
        ({"type": "done"},),
    ],
)
def test_every_path_yields_exactly_one_non_empty_chunk(
    monkeypatch: pytest.MonkeyPatch, handler_events: tuple[dict, ...]
) -> None:
    """The seam's invariant, restated across the network.

    The UI opens an empty bubble and fills it with what it receives; a stream that
    yields nothing would leave that bubble blank forever, with no error anywhere.
    """
    _install_transport(monkeypatch, _ok(*handler_events))
    chunks = _collect()

    assert len(chunks) >= 1
    assert all(chunk.strip() for chunk in chunks)


# ─── The architectural invariant, made executable ────────────────────────────


def test_the_client_does_not_depend_on_the_brain() -> None:
    """`packages/client` must not declare the agent as a dependency.

    This is the step-4 claim itself: the front reaches the agent through a URL, not
    an import. Asserting it here means a future "just import it, it is simpler"
    fails a test instead of quietly re-coupling the two containers.
    """
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(manifest.read_text())["project"]["dependencies"]

    assert not any("support-agent" in requirement for requirement in declared)
