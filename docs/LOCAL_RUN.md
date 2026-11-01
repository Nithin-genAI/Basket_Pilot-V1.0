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

Paste a list, give an address label, review the optimized plan, and type `add to cart` to authorize the cart update. The terminal then displays the cart readback and checkout preview. It never places an order or initiates payment. Type `/quit` to end the terminal client.

For a live Zepto run, first establish the MCP OAuth session and set `BP_USE_MOCK=false`. Set `ZEPTO_ADDRESS_ID` locally; its presence is the standing instruction to select that saved address automatically for each new basket. The search worker selects it in the MCP session before searching. A saved address by itself does not select a store. Live cart updates also require `BP_ALLOW_CART_WRITES=true`; the agent still waits for the separate `add to cart` confirmation after displaying the optimized plan. Keep `BP_ALLOW_ORDERS=false`: order placement and payment are not part of this workflow. Never put credentials or an address into an ACP message.

Zepto's captured `view_cart` contract does not expose bill totals, item prices, or itemized fee amounts. When those fields are absent, the preview marks the displayed subtotal-based total as estimated and lists validation warnings rather than presenting it as a verified checkout bill.

## Verify

```sh
.venv/bin/python -m unittest -v
```

The agent integration test uses a local loopback Bureau and mock adapters only.
