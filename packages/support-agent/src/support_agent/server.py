"""The HTTP door: the agent as a callable service.

Transports what `api.stream_reply` produces and holds no agent logic of its own — no
graph, no prompt, no decision about the reply. Written by hand rather than on the
LangGraph deployment rail, whose public contract IS the graph and would hand clients the
token stream the output guard must inspect first.

    make serve
    curl -N -X POST localhost:8000/chat -H 'X-API-Key: dev-key' -d '{"message": "…"}'
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from support_agent.api import get_agent, stream_reply
from support_agent.config import Settings, get_settings

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)

logging.getLogger("httpx").setLevel(logging.WARNING)

_SSE_FRAME = "data: {payload}\n\n"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

DEMO_USER_ID = "demo-user"


class ChatRequest(BaseModel):
    """One customer message, plus the keys that decide what the agent remembers."""

    message: str = Field(min_length=1, max_length=10_000)

    thread_id: str | None = Field(default=None, max_length=200)

    user_id: str | None = Field(default=None, max_length=200)


def _resolve_user_id(request: ChatRequest) -> str:
    """Decide which customer this request speaks for.

    Known security hole, deliberately isolated here: the identity is read from the body,
    not proven. Closing it means verifying a token in this one function.
    """
    return request.user_id or DEMO_USER_ID


def _header_bytes(value: str | None) -> bytes:
    """Recover the raw bytes of a header value Starlette decoded as latin-1.

    A missing header becomes `b""`, which simply fails the comparison — no special case,
    one single rejection path.
    """
    if value is None:
        return b""
    try:
        return value.encode("latin-1")
    except UnicodeEncodeError:
        return value.encode("utf-8")


async def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Reject callers that do not present the service key.

    Authorizes the CALLER, not the customer (`user_id`). `compare_digest` avoids leaking
    how much of the key was guessed right, and the comparison runs on BYTES: it raises
    `TypeError` on a `str` holding non-ASCII, so a header byte >= 0x80 used to turn a
    401 into a 500.
    """
    if settings.api_allow_unauthenticated:
        return
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server misconfigured: no API key set.",
        )
    if not secrets.compare_digest(
        _header_bytes(x_api_key), settings.api_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Refuse an insecure start, then warm the agent up before serving traffic.

    Fails closed: no key and no explicit opt-out means the process does not start.
    Builds the graph here rather than on the first request — it costs the embeddings
    probe plus the whole FAQ index — so `/ready` can answer truthfully and the first
    customer does not pay for it. uvicorn opens its port only once this returns, so
    whatever fails here must say why in the log.
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

    started = time.perf_counter()
    await asyncio.to_thread(get_agent)
    app.state.ready = True
    logger.info("Agent warmed up in %.2f s; ready to serve.", time.perf_counter() - started)
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

    Separate from `/ready`: a failing liveness probe means RESTART me, a failing
    readiness probe means STOP SENDING me traffic. Wiring liveness to the agent's state
    would kill a warming-up container in a loop.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, bool]:
    """Readiness: is the agent built and able to answer?"""
    return {"ready": bool(getattr(app.state, "ready", False))}


def _sse(event_type: str, **fields: str) -> str:
    """Serialize one typed SSE event.

    Typed JSON rather than raw text, so new event types (progress steps, FAQ sources)
    can be added without breaking existing clients.
    """
    return _SSE_FRAME.format(payload=json.dumps({"type": event_type, **fields}))


async def _events(message: str, *, user_id: str, thread_id: str) -> AsyncIterator[str]:
    """Turn the seam's chunks into SSE events.

    HTTP status codes travel with the first byte, so once streaming has begun a 200 can
    no longer become a 500: an error has to travel as an event inside the stream.
    Clients must treat `type: "error"` as a failure despite the 200.
    """
    try:
        async for chunk in stream_reply(message, user_id=user_id, thread_id=thread_id):
            yield _sse("chunk", text=chunk)
    except Exception:
        logger.exception("Streaming failed for thread_id=%s.", thread_id)
        yield _sse("error", message="Le service de support est momentanément indisponible.")
    yield _sse("done")


@app.post("/chat", dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest) -> StreamingResponse:
    """Answer one customer message as a stream of SSE events.

    POST because the message belongs in a body, which rules out the browser's native
    `EventSource` (GET only) — a web client uses `fetch` plus a `ReadableStream` reader.
    The contract is the stream, even though the seam currently delivers exactly one
    chunk.
    """
    thread_id = request.thread_id or secrets.token_urlsafe(16)
    user_id = _resolve_user_id(request)

    return StreamingResponse(
        _events(request.message, user_id=user_id, thread_id=thread_id),
        media_type="text/event-stream",
        headers={
            **_SSE_HEADERS,
            "X-Thread-Id": thread_id,
            "X-User-Id": user_id,
        },
    )
