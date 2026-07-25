"""The seam, now across the network (deployment step 4).

Until step 3 the front called `support_agent.api.stream_reply` — a plain Python
call, same process, shared memory. Two containers share no process, so the client
can no longer import the brain: it knows a URL and nothing else.

    BEFORE   app.py ──import──► stream_reply() ──► [ graph ]      one process
    AFTER    app.py ──import──► stream_reply() ──HTTP/SSE──► agent-api ──► [ graph ]
                                 ↑ this module     ↑ the only thing we know: a URL

The signature is deliberately IDENTICAL to the in-process seam
(`stream_reply(message, *, user_id, thread_id) -> AsyncIterator[str]`). That is
the whole demonstration of step 4: swapping a local call for a network call cost
`app.py` exactly one import line. A seam that has to be re-negotiated when the
transport changes was never a seam.

Two things the network adds, that an in-process call never had:

1. FAILURE MODES. Connection refused, a 401, a timeout, a truncated stream. The
   in-process seam promised "every path delivers exactly one non-empty chunk";
   this module is now the seam, so it owes the same promise. Every failure is
   logged with its real cause and rendered as ONE customer-facing sentence —
   otherwise a network blip would show up in the UI as an empty bubble.

2. TWO IDENTITIES ON THE WIRE. `AGENT_API_KEY` authorizes the CALLER (may this
   client use the agent at all?); `user_id` says WHO we are talking about. They
   travel differently on purpose: the key in a header, the identity in the body.
   Conflating them is how one customer ends up reading another's history. The
   body-borne `user_id` is still unproven — that is deployment step 5, and it is
   closed on the SERVER side (`server._resolve_user_id`), not here: a client can
   never be the thing that proves its own identity.

This module holds no UI code (no `chainlit` import) and no agent code (no
`langgraph`, no `support_agent`). It is pure transport, and that is why the same
file would serve a React front through a `fetch` call.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from httpx_sse import aconnect_sse

logger = logging.getLogger(__name__)

# Where the agent lives. The default is the host-side dev setup (`make serve`);
# in Compose it becomes `http://agent-api:8000` — the SERVICE NAME, resolved by
# Docker's internal DNS, not localhost (two containers = two network stacks).
DEFAULT_AGENT_API_URL = "http://localhost:8000"

# Split timeouts, because the two numbers answer different questions. `connect`
# is "is anything listening?" — a few seconds is generous. `read` is "how long may
# the agent think?", and the answer is: quite long. The seam delivers the reply in
# ONE chunk once the output guard has cleared it, so nothing at all arrives until
# the whole turn is done (LLM hops included). A default 5 s timeout would cut off
# every real answer.
_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)

# Customer-facing fallbacks. They are deliberately vague about the cause: the
# real diagnosis goes to the logs, where the operator reads it. Telling a customer
# "401 from agent-api" would be both useless and a small information leak.
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

    One client per message, not a shared pool. The saving a pool would bring is a
    TCP handshake (single-digit milliseconds) against a turn that takes seconds,
    and a shared client would need somewhere to be closed — Chainlit gives a front
    no process-wide shutdown hook we could rely on. A pool that is never closed is
    a worse bug than a handshake that is paid twice.

    It is also the single place the transport is built, which is what makes this
    module testable offline: a test swaps this function for one that returns a
    client wired to `httpx.MockTransport`, and no socket is ever opened.
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

    Same contract as the in-process seam it replaces: yields plain strings, and
    always yields at least one non-empty chunk.

    Args:
        message: The customer's message.
        user_id: WHO we are talking to — the long-term memory key.
        thread_id: WHICH conversation this is — the short-term memory key.
            Reusing the same value continues the same remembered conversation.

    Yields:
        The customer-facing reply, chunk by chunk. Today the server sends exactly
        one `chunk` event (the output guard needs the complete reply before any of
        it may leave), but we consume a STREAM: that is the contract, and phase
        B1.5 will add events without touching this loop.
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
                # Check the status BEFORE iterating. `aiter_sse` demands a
                # `text/event-stream` response, so a 401 (which answers JSON)
                # would surface as an opaque parser error instead of the real
                # cause. Reading the body here is safe: nothing has been streamed.
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
                        # The server already phrased this for a customer, and it
                        # travels INSIDE a 200 because the status left with the
                        # first byte. Treat it as a failure, show it as a reply.
                        logger.error("agent-api reported a streaming error.")
                        delivered = True
                        yield event.get("message") or UNREACHABLE_MESSAGE
                    elif kind == "done":
                        break
                    else:
                        # Forward compatibility, promised by `server._sse`: new
                        # event types (progress steps, FAQ sources) must not break
                        # an older client. Ignoring them is the correct behaviour.
                        logger.debug("Ignoring unknown SSE event type: %r", kind)

    except httpx.HTTPError:
        # Connection refused, DNS failure, read timeout, connection reset. The
        # agent is unreachable or too slow; the customer must still see something.
        logger.exception("Could not reach agent-api at %s.", url)
        if not delivered:
            yield UNREACHABLE_MESSAGE
        return

    if not delivered:
        # The stream ended without a single chunk: the server crashed mid-answer,
        # or a proxy cut the connection after the headers. Rare, and exactly the
        # case a front must never render as silence.
        logger.error("agent-api closed the stream without delivering any reply.")
        yield TRUNCATED_MESSAGE
