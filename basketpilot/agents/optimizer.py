"""Pure deterministic optimizer worker."""

from __future__ import annotations

from uagents import Agent, Context

from basketpilot import messages
from basketpilot.agents.wire import item_from_wire, plan_to_wire, result_from_wire, schedule_from_wire
from basketpilot.optimizer.solve import optimize


def create_optimizer_agent(seed: str, port: int) -> Agent:
    agent = Agent(name="bp-optimizer", seed=seed, port=port, network="testnet")

    @agent.on_message(model=messages.OptimizeRequest)
    async def optimize_request(ctx: Context, sender: str, msg: messages.OptimizeRequest) -> None:
        ctx.logger.info("Optimizer received %d platform results for %s", len(msg.results), msg.session_id)
        try:
            items = tuple(item_from_wire(row) for row in msg.items)
            results = tuple(result_from_wire(row) for row in msg.results)
            schedules = {row.platform: schedule_from_wire(row.fee_schedule) for row in msg.results}
            plan = optimize(items, results, schedules, {"multi_platform_penalty_paise": msg.multi_platform_penalty_paise})
            ctx.logger.info("Optimizer created plan with %d assignments for %s", len(plan.assignments), msg.session_id)
            await ctx.send(sender, plan_to_wire(msg.session_id, plan))
        except Exception as exc:
            ctx.logger.error("Optimizer failed for session %s (%s)", msg.session_id, type(exc).__name__)
            raise

    return agent
