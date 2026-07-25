"""The Chainlit demo shell (Phase B1.2): "Hello Chainlit".

The whole point of this file is what it does NOT contain: no graph, no node, no
LangGraph import. It talks to the agent through the single seam `stream_reply`.
That is what keeps the front throwaway — we could swap Chainlit for React
without touching the brain.

⭐ Deployment step 4 happened HERE, and it is one line: `stream_reply` used to be
imported from `support_agent` (same process, a Python call) and now comes from
`client_chainlit.agent_client` (another container, an HTTP/SSE call). The handler
below did not change by a single character, because the network client exposes
the SAME signature. That is the seam being worth its cost, demonstrated rather
than claimed — and `packages/client` no longer declares `support-agent` as a
dependency at all, so the client's image cannot even contain the brain.

Chainlit lifecycle used here:

    @cl.on_chat_start  -> runs once when a browser opens a new chat
    @cl.on_message     -> runs on every user message

Run it:

    chainlit run packages/client/src/client_chainlit/app.py -w

Phase scope (deliberately minimal — one thing at a time):
- B1.2: wire it end to end and show the full reply once it is ready.
- B1.3: consume the seam as a STREAM (`stream_token`) rather than a single
  string. Note that the reply currently arrives as one chunk: the agent's output
  guard must inspect the whole text before any of it may be shown, so token-level
  streaming was removed from the seam on purpose (see `docs/streaming.md`). The
  streaming CONTRACT stays — only the chunk size changed.
- B1.4 (later): formalize session / thread_id / user identity.
- B1.5 (later): show the graph's steps, so the wait is legible instead of silent.
"""

from __future__ import annotations

import chainlit as cl

from client_chainlit.agent_client import stream_reply

# Simulated customer id: no auth in the demo (the long-term memory key). Made
# real in level 2, where auth derives `user_id` from a logged-in identity.
DEMO_USER_ID = "demo-user"

# Last-resort text if the seam ever yields nothing at all. It should be
# unreachable (the seam always delivers exactly one chunk), which is why it reads
# like a bug report rather than a support reply: seeing it means the contract
# broke, and a silent empty bubble would hide that.
EMPTY_REPLY_MESSAGE = (
    "⚠️ Aucune réponse n'a été reçue de l'agent. Consultez les logs du serveur."
)


@cl.on_chat_start
async def on_chat_start() -> None:
    """Greet the visitor when a new chat opens."""
    await cl.Message(
        content="Bonjour 👋 Je suis votre assistant de support. Comment puis-je vous aider ?"
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Forward one user message to the agent and render its reply.

    We open an empty message and push every chunk the seam yields
    (`stream_token`), then finalize (`update`). The seam currently delivers the
    reply in ONE chunk — the agent's output guard has to inspect the complete text
    before anything may be shown (see `docs/streaming.md`) — but we keep consuming
    it as a stream: that is the contract, and the day the seam yields more chunks
    (Phase B1.5's step-by-step progress) this handler is already right.

    `stream_token` is what actually creates the message server-side, so a stream
    that yields NOTHING would leave us calling `update()` on a message that was
    never sent. The seam now guarantees at least one chunk, but the front does not
    get to assume that — a UI must never depend on a promise made across a seam,
    so we fall back to sending a visible message instead of silently showing
    nothing.

    Note how little the front does: it never touches LangGraph. All the complexity
    (running the graph, guarding the reply, handling escalation) lives behind
    `stream_reply` — since step 4, behind an HTTP call as well. That is the seam
    paying off — the classic Chainlit tutorial would call `graph.stream(...)` right
    here; we deliberately do not, and now we physically could not.

    We use the Chainlit session id as the short-term-memory `thread_id`, so the
    agent remembers this conversation across turns. Formalizing identity
    (per-user isolation, simulating several users) is Phase B1.4.
    """
    thread_id = cl.context.session.id

    reply = cl.Message(content="")
    received = False
    async for chunk in stream_reply(
        message.content, user_id=DEMO_USER_ID, thread_id=thread_id
    ):
        received = True
        await reply.stream_token(chunk)

    if received:
        await reply.update()
    else:
        reply.content = EMPTY_REPLY_MESSAGE
        await reply.send()
