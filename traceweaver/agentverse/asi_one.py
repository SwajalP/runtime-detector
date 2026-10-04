"""ASI:One-facing chat handler.

ASI:One speaks the official ``AgentChatProtocol``
(``uagents_core.contrib.protocols.chat``): ``ChatMessage`` in,
``ChatAcknowledgement`` plus a ``ChatMessage`` reply out.

When that package is importable we expose it on the uAgent. Text content is
routed through :meth:`TraceWeaverService.handle_chat_text` (same path as the
structured ``ChatText`` fallback on ``TraceWeaverContextProtocol``). Session
start with no text returns the help card.

If the official protocol cannot be imported, ``ChatText`` remains the
only chat surface — see ``docs/AGENTVERSE.md``.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from traceweaver.agentverse.service import HELP_TEXT, TraceWeaverService

try:
    from uagents import Context, Protocol
    from uagents_core.contrib.protocols.chat import (
        ChatAcknowledgement,
        ChatMessage,
        EndSessionContent,
        StartSessionContent,
        TextContent,
        chat_protocol_spec,
    )

    ASI_ONE_CHAT_AVAILABLE = True
except Exception:  # official chat protocol not installed
    ASI_ONE_CHAT_AVAILABLE = False
    Context = Protocol = object  # type: ignore[misc,assignment]
    ChatAcknowledgement = ChatMessage = TextContent = object  # type: ignore[misc,assignment]
    chat_protocol_spec = None


# Replace after `traceweaver agentverse --mailbox` registers on Agentverse.
AGENTVERSE_ADDRESS_PLACEHOLDER = "agent1q<TRACEWEAVER_RUNTIME_AGENTVERSE_ADDRESS>"


def build_asi_one_protocol(service: TraceWeaverService, pool: ThreadPoolExecutor):
    """Return an AgentChatProtocol, or None if the official package is missing."""
    if not ASI_ONE_CHAT_AVAILABLE:
        return None

    proto = Protocol(spec=chat_protocol_spec)
    loop_run = lambda fn, *args: asyncio.get_event_loop().run_in_executor(pool, fn, *args)

    @proto.on_message(model=ChatAcknowledgement)
    async def on_asi_one_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        return

    @proto.on_message(model=ChatMessage)
    async def on_asi_one_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        texts: list[str] = []
        started = ended = False
        for item in msg.content or []:
            if isinstance(item, StartSessionContent):
                started = True
            elif isinstance(item, EndSessionContent):
                ended = True
            elif isinstance(item, TextContent):
                texts.append(item.text or "")
        if ended and not texts:
            return
        if started and not texts:
            reply = HELP_TEXT
        else:
            reply, _payload = await loop_run(service.handle_chat_text, " ".join(texts).strip() or "help", sender)
        await ctx.send(sender, ChatMessage(content=[TextContent(text=reply)]))

    return proto
