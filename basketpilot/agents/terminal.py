"""Interactive local ACP client used by scripts/chat_client.py."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent, StartSessionContent,
    TextContent, chat_protocol_spec,
)


InputFunction = Callable[[str], Awaitable[str]]
StopFunction = Callable[[], None]


def create_terminal_agent(
    orchestrator_address: str, seed: str, port: int, initial_text: str,
    input_function: InputFunction, stop_function: StopFunction | None = None,
) -> Agent:
    agent = Agent(name="bp-terminal-chat-client", seed=seed, port=port, network="testnet")
    chat = Protocol(spec=chat_protocol_spec)
    waiting_for_user = False

    async def prompt_and_send(ctx: Context, prompt: str) -> None:
        nonlocal waiting_for_user
        if waiting_for_user:
            return
        waiting_for_user = True
        try:
            text = (await input_function(prompt)).strip()
            if text.casefold() in {"/quit", "quit", "exit"}:
                await ctx.send(orchestrator_address, ChatMessage(content=[EndSessionContent(type="end-session")]))
                if stop_function is not None:
                    stop_function()
                return
            await ctx.send(orchestrator_address, ChatMessage(content=[TextContent(type="text", text=text)]))
        finally:
            waiting_for_user = False

    @agent.on_event("startup")
    async def start(ctx: Context) -> None:
        ctx.logger.info("Terminal ACP client ready for %s", orchestrator_address)
        await ctx.send(orchestrator_address, ChatMessage(content=[
            StartSessionContent(type="start-session"), TextContent(type="text", text=initial_text),
        ]))

    @chat.on_message(ChatMessage)
    async def receive_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        nonlocal waiting_for_user
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        text = "\n".join(content.text for content in msg.content if isinstance(content, TextContent))
        if text:
            print(f"\nBasketPilot: {text}")
        lowered = text.casefold()
        prompt = None
        if "type `select saved address`" in lowered:
            prompt = "Type select saved address to authorize this selection (or /quit): "
        elif "which delivery address" in lowered:
            prompt = "Address label (or /quit): "
        elif "please clarify these lines" in lowered:
            prompt = "Clarified full grocery list (or /quit): "
        elif "reply exactly `add to cart`" in lowered:
            prompt = "Type add to cart to authorize the cart update (or /quit): "
        elif "preview complete. no order was placed" in lowered:
            prompt = "Session complete. Enter a new grocery list or /quit: "
        if prompt:
            asyncio.create_task(prompt_and_send(ctx, prompt))

    @chat.on_message(ChatAcknowledgement)
    async def receive_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.debug("Orchestrator acknowledged %s", msg.acknowledged_msg_id)

    agent.include(chat, publish_manifest=False)
    return agent
