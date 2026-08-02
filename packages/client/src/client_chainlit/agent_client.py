"""The seam, across the network.

The client cannot import the brain — two containers share no process — so it knows a URL
and nothing else. The signature is deliberately IDENTICAL to the in-process seam, which
is why swapping a local call for a network call cost `app.py` exactly one import line.

Two things the network adds. FAILURE MODES: connection refused, a 401, a timeout, a
truncated stream. This module is now the seam, so it owes the same promise — every
failure is logged with its real cause and rendered as ONE customer-facing sentence,
never an empty bubble. TWO IDENTITIES ON THE WIRE: `AGENT_API_KEY` authorizes the CALLER
and travels in a header, `user_id` says who we are talking about and travels in the
body. The body-borne `user_id` is still unproven, and it is the SERVER that must close
that — a client can never be the thing that proves its own identity.

No UI code and no agent code: pure transport, which is why the same file would serve a
React front through a `fetch` call.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from httpx_sse import aconnect_sse

logger = logging.getLogger(__name__)

DEFAULT_AGENT_API_URL = "http://localhost:8000"

_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)

UNREACHABLE_MESSAGE = (
    "⚠️ Le service de support est momentanément injoignable. Merci de réessayer "
    "dans un instant."
)
REJECTED_MESSAGE = (
    "⚠️ Le service de support a refusé la demande. L'équipe technique a été notifiée."
)
TRUNCATED_MESSAGE = (
    "⚠️ La réponse du service de support est arrivée incomplète. Merci de réessayer."
)


def get_agent_api_url() -> str:
    """Return the agent's base URL, read at CALL time.

    Read here rather than at import time so that a test — or a `.env` loaded by
    Chainlit after this module is imported — can still change it. Module-level
    constants freeze whatever the environment happened to be at import.
    """
    return os.getenv("AGENT_API_URL", DEFAULT_AGENT_API_URL).rstrip("/")


def get_agent_api_key() -> str | None:
    """Return the service key this client presents, or None if it has none.

    None is legitimate: a local run with `API_ALLOW_UNAUTHENTICATED=true` on the
    server. We send no header at all in that case rather than an empty one — an
    empty `X-API-Key` would be a wrong key, not a missing one.
    """
    return os.getenv("AGENT_API_KEY") or None


def _new_client() -> httpx.AsyncClient:
    """Create the HTTP client used for ONE turn.

    One client per message, not a shared pool. The saving a pool would bring is a TCP
    handshake against a turn that takes seconds, and Chainlit gives a front no process-
    wide shutdown hook — a pool that is never closed is a worse bug.

    It is also the single place the transport is built, which is what makes this module
    testable offline against `httpx.MockTransport`, with no socket opened.
    """
    return httpx.AsyncClient(timeout=_TIMEOUT)


def _decode(raw: str) -> dict | None:
    """Decode one SSE payload, tolerating garbage rather than crashing the turn."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed SSE payload: %r", raw[:200])
        return None
    return payload if isinstance(payload, dict) else None


async def stream_reply(
    message: str,
    *,
    user_id: str,
    thread_id: str,
) -> AsyncIterator[str]:
    """Stream the agent's reply to one customer message, over HTTP.

    Same contract as the in-process seam it replaces: yields plain strings, and always
    yields at least one non-empty chunk.

    Args:
        message: The customer's message.
        user_id: WHO we are talking to — the long-term memory key.
        thread_id: WHICH conversation this is — the short-term memory key. Reusing
            the same value continues the same remembered conversation.

    Yields:
        The customer-facing reply, chunk by chunk. Today the server sends exactly
        one `chunk` event, but we consume a STREAM: that is the contract.
    """
    url = f"{get_agent_api_url()}/chat"
    headers = {"Accept": "text/event-stream"}
    key = get_agent_api_key()
    if key:
        headers["X-API-Key"] = key

    payload = {"message": message, "user_id": user_id, "thread_id": thread_id}
    delivered = False

    try:
        async with _new_client() as client:
            async with aconnect_sse(
                client, "POST", url, json=payload, headers=headers
            ) as source:
                if source.response.status_code != httpx.codes.OK:
                    body = (await source.response.aread()).decode(errors="replace")
                    logger.error(
                        "agent-api rejected the request: HTTP %s — %s",
                        source.response.status_code,
                        body[:500],
                    )
                    yield REJECTED_MESSAGE
                    return

                async for sse in source.aiter_sse():
                    event = _decode(sse.data)
                    if event is None:
                        continue
                    kind = event.get("type")

                    if kind == "chunk":
                        text = event.get("text") or ""
                        if text:
                            delivered = True
                            yield text
                    elif kind == "error":
                        logger.error("agent-api reported a streaming error.")
                        delivered = True
                        yield event.get("message") or UNREACHABLE_MESSAGE
                    elif kind == "done":
                        break
                    else:
                        logger.debug("Ignoring unknown SSE event type: %r", kind)

    except httpx.HTTPError:
        logger.exception("Could not reach agent-api at %s.", url)
        if not delivered:
            yield UNREACHABLE_MESSAGE
        return

    if not delivered:
        logger.error("agent-api closed the stream without delivering any reply.")
        yield TRUNCATED_MESSAGE
