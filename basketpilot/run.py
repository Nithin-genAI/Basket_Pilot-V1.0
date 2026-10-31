"""Run the local BasketPilot agents in one uAgents Bureau."""

from __future__ import annotations

import os

from uagents import Bureau
from uagents_core.identity import Identity
from uagents_core.registration import BatchRegistrationPolicy
from uagents_core.types import AgentInfo

from basketpilot.agents.checkout import create_checkout_agent
from basketpilot.agents.optimizer import create_optimizer_agent
from basketpilot.agents.orchestrator import create_orchestrator_agent
from basketpilot.agents.parser import create_parser_agent
from basketpilot.agents.search import create_search_agent
from basketpilot.platforms.mock import default_mock_adapters


class LocalOnlyRegistrationPolicy(BatchRegistrationPolicy):
    """No-op Almanac/Agentverse policy for isolated local development."""

    async def register(self) -> None:
        return None

    def add_agent(self, agent_info: AgentInfo, identity: Identity) -> None:
        return None


def seed_for(name: str) -> str:
    key = f"BP_SEED_{name.replace('-', '_').upper()}"
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Set {key} in the environment; agent seeds are never generated or stored by BasketPilot.")
    return value


def build_bureau(*, port: int = 8000, session_store=None) -> tuple[Bureau, dict[str, object]]:
    if os.environ.get("BP_MAILBOX", "false").casefold() == "true":
        raise RuntimeError("BP_MAILBOX=true is not supported in this local-only Step 3B build.")
    use_mock = os.environ.get("BP_USE_MOCK", "true").casefold() == "true"
    if use_mock:
        adapters = default_mock_adapters()
    else:
        from basketpilot.platforms.zepto import ZeptoAdapter

        adapters = (ZeptoAdapter(address_id=os.environ.get("ZEPTO_ADDRESS_ID")),)

    adapter_by_name = {adapter.name: adapter for adapter in adapters}
    parser = create_parser_agent(seed_for("parser"), 8101)
    search_agents = {
        adapter.name: create_search_agent(adapter, seed_for(f"search_{adapter.name}"), 8110 + index)
        for index, adapter in enumerate(adapters)
    }
    optimizer = create_optimizer_agent(seed_for("optimizer"), 8120)
    checkout = create_checkout_agent(adapter_by_name, seed_for("checkout"), 8121)
    orchestrator = create_orchestrator_agent(
        seed=seed_for("orchestrator"), port=8100, parser_address=parser.address,
        search_addresses={name: worker.address for name, worker in search_agents.items()},
        optimizer_address=optimizer.address, checkout_address=checkout.address,
        store=session_store,
    )
    bureau = Bureau(port=port, registration_policy=LocalOnlyRegistrationPolicy(), log_level="INFO", network="testnet")
    for worker in (parser, *search_agents.values(), optimizer, checkout, orchestrator):
        bureau.add(worker)
    return bureau, {
        "orchestrator": orchestrator,
        "parser": parser,
        "search": search_agents,
        "optimizer": optimizer,
        "checkout": checkout,
        "adapters": adapters,
    }


def main() -> None:
    os.environ.setdefault("BP_USE_MOCK", "true")
    os.environ.setdefault("BP_MAILBOX", "false")
    os.environ.setdefault("BP_ALLOW_ORDERS", "false")
    bureau, agents = build_bureau(port=int(os.environ.get("BP_BUREAU_PORT", "8000")))
    print("BasketPilot local Bureau (no Agentverse registration)")
    for role, value in agents.items():
        if role == "search":
            for name, worker in value.items():
                print(f"search {name}: {worker.address}")
        elif hasattr(value, "address"):
            print(f"{role}: {value.address}")
    bureau.run()


if __name__ == "__main__":
    main()
