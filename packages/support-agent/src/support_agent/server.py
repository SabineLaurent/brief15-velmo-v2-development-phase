"""The HTTP door (deployment step 1): the agent as a callable service.

This module TRANSPORTS what `api.stream_reply` PRODUCES. It holds no agent logic
of its own — no graph, no node, no prompt, no decision about the reply. Adding
any here would create a second brain that only the HTTP path can reach, and the
CLI would silently diverge from the deployed service.

    client  ──HTTP/SSE──►  server.py  ──►  stream_reply(...)  ──►  [ graph hidden ]
                           ↑ transport      ↑ THE SEAM (the contract)

Why we write this ourselves instead of using the LangGraph deployment rail: that
rail's public contract IS the graph (`/runs/stream` lets the caller pick
`stream_mode`), which would hand clients back the token stream the output guard
must inspect first — the very bypass fixed in 519f254. It also duplicates our
checkpointer/store and requires a LangSmith key even locally. Full reasoning:
`docs/plan-deploiement-2026-07-25.md` §5.

Run it:

    make serve      # uvicorn support_agent.server:app --reload

Call it:

    curl -N -X POST localhost:8000/chat \
      -H 'X-API-Key: dev-key' -H 'Content-Type: application/json' \
      -d '{"message": "Où est ma commande O-2024-0103 ?"}'
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from support_agent.api import get_agent, stream_reply
from support_agent.config import Settings, get_settings

logger = logging.getLogger(__name__)

# SSE (Server-Sent Events) frame: one event is `data: <payload>` followed by a
# BLANK line — that blank line is what tells the client the event is complete.
_SSE_FRAME = "data: {payload}\n\n"

# Streaming needs these or intermediaries defeat it: proxies love to buffer a
# response until it is complete, which would turn our stream back into one big
# blocking answer. `X-Accel-Buffering` is nginx-specific but harmless elsewhere.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Fallback identity while `user_id` is not yet proven (see `_resolve_user_id`).
DEMO_USER_ID = "demo-user"


class ChatRequest(BaseModel):
    """One customer message, plus the keys that decide what the agent remembers."""

    message: str = Field(min_length=1, max_length=10_000)

    # WHICH conversation (short-term memory). The client owns this: reusing the
    # same value continues the same remembered thread. Absent = a fresh thread,
    # minted server-side, so a caller that does not care about memory still works.
    thread_id: str | None = Field(default=None, max_length=200)

    # WHO we are talking to (long-term memory). Trusted from the body TODAY only
    # because nothing proves identity yet — see `_resolve_user_id`.
    user_id: str | None = Field(default=None, max_length=200)


def _resolve_user_id(request: ChatRequest) -> str:
    """Decide WHICH customer this request speaks for.

    🔴 This function is the known security hole, deliberately isolated in one
    place. Today it trusts the body, so any caller can read any customer's
    long-term memory by changing a string. That is survivable right now — the
    service is not public yet — and it is exactly what deployment step 5 closes:
    the client will send a PROOF (a verified token) instead of an identity, and
    only this function changes. See `docs/architecture-cible-2026-07-25.md` §5.1.
    """
    return request.user_id or DEMO_USER_ID


async def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Reject callers that do not present the service key.

    This authorizes the CALLER (may you use this agent at all?), not the customer
    (`user_id`). Two different questions; conflating them is how agents end up
    letting one customer read another's history.

    `compare_digest` instead of `==`: a plain comparison returns as soon as two
    bytes differ, so its duration leaks how much of the key was guessed right.
    """
    if settings.api_allow_unauthenticated:
        return
    # Cannot happen: the lifespan refuses to start in this state. Kept as a
    # belt-and-braces check so a future refactor of startup cannot open the door.
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server misconfigured: no API key set.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Refuse an insecure start, then warm the agent up before serving traffic.

    Two jobs, both about not lying to whoever calls us.

    1. FAIL CLOSED. No key and no explicit opt-out => the process does not start.
       A server that quietly runs wide open is worse than one that refuses to
       boot, because nothing ever tells you.

    2. Build the graph HERE, not on the first request. Building it costs the
       embeddings probe plus the whole FAQ index (~1.6 s today), and it runs in a
       worker thread so the event loop stays free. Doing it at startup means
       `/ready` can answer truthfully, and the first customer does not pay for it.
       ⚠️ This is also what makes scale-to-zero expensive on Azure Container
       Apps: every cold start re-indexes. See the plan doc, step 5.
    """
    settings = get_settings()

    if not settings.api_key and not settings.api_allow_unauthenticated:
        raise RuntimeError(
            "Refusing to start without authentication. Set API_KEY=<secret>, or "
            "set API_ALLOW_UNAUTHENTICATED=true if this really is a local run on "
            "a private network."
        )
    if settings.api_allow_unauthenticated:
        logger.warning(
            "AUTHENTICATION DISABLED (API_ALLOW_UNAUTHENTICATED=true). Anyone who "
            "can reach this port can spend your LLM credits. Local runs only."
        )

    await asyncio.to_thread(get_agent)
    app.state.ready = True
    logger.info("Agent warmed up; ready to serve.")
    yield


app = FastAPI(
    title="Support agent API",
    summary="The agent's one HTTP door. Wraps the `stream_reply` seam.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: is this process alive? No dependency is touched on purpose.

    Kept separate from `/ready` because they answer different questions, and an
    orchestrator reacts differently to each: a failing liveness probe means
    RESTART me, a failing readiness probe means STOP SENDING me traffic. Wiring
    liveness to the agent's state would get a warming-up container killed in a
    loop before it ever managed to serve.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, bool]:
    """Readiness: is the agent built and able to answer?"""
    return {"ready": bool(getattr(app.state, "ready", False))}


def _sse(event_type: str, **fields: str) -> str:
    """Serialize ONE typed SSE event.

    Typed JSON rather than raw text, decided up front: the day the seam yields
    progress steps or FAQ sources (phase B1.5), they become new `type` values
    that old clients can ignore. Raw text would have forced a breaking change on
    every consumer — and this endpoint is meant to outlive Chainlit.
    """
    return _SSE_FRAME.format(payload=json.dumps({"type": event_type, **fields}))


async def _events(message: str, *, user_id: str, thread_id: str) -> AsyncIterator[str]:
    """Turn the seam's chunks into SSE events.

    The error branch matters more than it looks: HTTP status codes are sent with
    the FIRST byte of the response, so once streaming has begun we can no longer
    turn a 200 into a 500. An error therefore has to travel as an event inside
    the stream. Clients must treat `type: "error"` as a failure even though the
    response said 200.
    """
    try:
        async for chunk in stream_reply(message, user_id=user_id, thread_id=thread_id):
            yield _sse("chunk", text=chunk)
    except Exception:
        # The seam already swallows graph failures and answers gracefully, so
        # reaching here means the seam ITSELF broke (bad config, crash on import).
        logger.exception("Streaming failed for thread_id=%s.", thread_id)
        yield _sse("error", message="Le service de support est momentanément indisponible.")
    yield _sse("done")


@app.post("/chat", dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest) -> StreamingResponse:
    """Answer one customer message as a stream of SSE events.

    POST, not GET, because the message belongs in a body — which means the
    browser's native `EventSource` cannot consume this (it only does GET). A web
    client uses `fetch` + a `ReadableStream` reader instead. That is a deliberate
    trade: correct HTTP semantics over one convenient browser API.

    The response is a stream even though the seam currently delivers exactly ONE
    chunk (the output guard needs the complete reply before any of it may leave —
    see `api.py`). The CONTRACT is the stream; the chunk count is free to grow.
    """
    thread_id = request.thread_id or secrets.token_urlsafe(16)
    user_id = _resolve_user_id(request)

    return StreamingResponse(
        _events(request.message, user_id=user_id, thread_id=thread_id),
        media_type="text/event-stream",
        headers={
            **_SSE_HEADERS,
            # The keys we actually used, so a caller that sent none can keep
            # talking to the same remembered thread on its next request.
            "X-Thread-Id": thread_id,
            "X-User-Id": user_id,
        },
    )
