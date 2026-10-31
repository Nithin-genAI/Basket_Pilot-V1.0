"""Stateless parsing worker."""

from __future__ import annotations

from uagents import Agent, Context

from basketpilot import messages
from basketpilot.llm import parse_list
from basketpilot.agents.wire import item_to_wire


def create_parser_agent(seed: str, port: int) -> Agent:
    agent = Agent(name="bp-parser", seed=seed, port=port, network="testnet")

    @agent.on_message(model=messages.ParseRequest)
    async def parse_request(ctx: Context, sender: str, msg: messages.ParseRequest) -> None:
        parsed = parse_list(msg.text)
        ctx.logger.info("Parsed request %s into %d items", msg.session_id, len(parsed.items))
        await ctx.send(sender, messages.ParsedList(
            session_id=msg.session_id,
            items=[item_to_wire(item) for item in parsed.items],
            ambiguities=list(parsed.ambiguities),
        ))

    return agent
