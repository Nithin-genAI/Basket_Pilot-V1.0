# BasketPilot Step 3B: local agents

This step uses a single uAgents Bureau with local message dispatch. It does not register with Agentverse, use a mailbox, call ASI-1, or place live orders. `basketpilot/llm.py` exposes the future ASI-1 interface and uses deterministic fallbacks.

## Install and configure

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and replace every `BP_SEED_*` placeholder with a stable private value. The seeds determine stable local agent addresses; never commit `.env`. Then export the values in the current shell:

```sh
set -a
source .env
set +a
```

## Run

Start the local Bureau without the terminal chat client:

```sh
BP_USE_MOCK=true BP_MAILBOX=false BP_ALLOW_ORDERS=false .venv/bin/python -m basketpilot.run
```

For a terminal-driven conversation, run this command instead; it starts the same agents and a local ACP client in one Bureau:

```sh
BP_USE_MOCK=true BP_MAILBOX=false BP_ALLOW_ORDERS=false .venv/bin/python scripts/chat_client.py
```

Paste a list, give an address label, review the plan and preview, then type `confirm` for the dry-run result. Type `/quit` to end the terminal client.

`BP_USE_MOCK=false` selects the existing Zepto adapter for read/search work. Live cart updates remain blocked unless `BP_ALLOW_CART_WRITES=true`; live order placement remains blocked in this Step 3B runtime, even if `BP_ALLOW_ORDERS` is set. The MCP address ID, if needed for reads, can be supplied with `ZEPTO_ADDRESS_ID` in the environment. Never put credentials or an address into an ACP message.

## Verify

```sh
.venv/bin/python -m unittest -v
```

The agent integration test uses a local loopback Bureau and mock adapters only.
