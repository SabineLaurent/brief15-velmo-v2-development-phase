"""The Chainlit demo shell (Phase B1.2): "Hello Chainlit".

The whole point of this file is what it does NOT contain: no graph, no node, no
LangGraph import. It talks to the agent through the single seam `stream_reply`
(see `support_agent.api`). That is what keeps the front throwaway — we could
swap Chainlit for React without touching the brain.

Chainlit lifecycle used here:

    @cl.on_chat_start  -> runs once when a browser opens a new chat
    @cl.on_message     -> runs on every user message

Run it:

    chainlit run packages/frontend/src/frontend/app.py -w

Phase scope (deliberately minimal — one thing at a time):
- B1.2: wire it end to end and show the full reply once it is ready.
- B1.3 (here): stream the reply token by token as it is generated.
- B1.4 (later): formalize session / thread_id / user identity.
"""

from __future__ import annotations

import chainlit as cl

from support_agent import stream_reply

# Simulated customer id: no auth in the demo (the long-term memory key). Made
# real in level 2, where auth derives `user_id` from a logged-in identity.
DEMO_USER_ID = "demo-user"


@cl.on_chat_start
async def on_chat_start() -> None:
    """Greet the visitor when a new chat opens."""
    await cl.Message(
        content="Bonjour 👋 Je suis votre assistant de support. Comment puis-je vous aider ?"
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Forward one user message to the agent and stream its reply live.

    B1.3: instead of waiting for the whole reply, we open an empty message and
    push each token from the seam as it arrives (`stream_token`), then finalize
    (`update`). This is what makes the chat feel alive — a reply that lands in
    one block after several seconds is perceived as broken.

    Note how little the front does: it never touches LangGraph. All the streaming
    complexity (running the graph, filtering the router) lives behind
    `stream_reply`. That is the seam paying off — the classic Chainlit tutorial
    would call `graph.stream(...)` right here; we deliberately do not.

    We use the Chainlit session id as the short-term-memory `thread_id`, so the
    agent remembers this conversation across turns. Formalizing identity
    (per-user isolation, simulating several users) is Phase B1.4.
    """
    thread_id = cl.context.session.id

    reply = cl.Message(content="")
    async for token in stream_reply(
        message.content, user_id=DEMO_USER_ID, thread_id=thread_id
    ):
        await reply.stream_token(token)
    await reply.update()
