"""The Chainlit demo shell.

The point of this file is what it does NOT contain: no graph, no node, no LangGraph
import. It talks to the agent through the single seam `stream_reply`, which since the
client became its own container is an HTTP/SSE call — `packages/client` no longer
declares `support-agent` as a dependency at all, so the client's image cannot even
contain the brain.

    @cl.on_chat_start  -> runs once when a browser opens a new chat
    @cl.on_message     -> runs on every user message

    chainlit run packages/client/src/client_chainlit/app.py -w
"""

from __future__ import annotations

import chainlit as cl

from client_chainlit.agent_client import stream_reply

DEMO_USER_ID = "demo-user"

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

    We open an empty message, push every chunk the seam yields (`stream_token`), then
    finalize (`update`). The seam currently delivers the reply in ONE chunk — the output
    guard has to inspect the complete text before anything may be shown — but we keep
    consuming it as a stream, because that is the contract.

    `stream_token` is what actually creates the message server-side, so a stream that
    yields NOTHING would leave us calling `update()` on a message that was never sent. A
    UI must never depend on a promise made across a seam, hence the visible fallback.

    The Chainlit session id is used as the short-term-memory `thread_id`, so the agent
    remembers this conversation across turns.
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
