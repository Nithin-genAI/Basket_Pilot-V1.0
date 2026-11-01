"""Run a complete local Bureau and drive it through ACP from the terminal."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading

# Running ``python scripts/chat_client.py`` puts ``scripts/`` on sys.path,
# not the project root. Add the root so the documented direct command works
# without requiring an editable install or a PYTHONPATH override.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from basketpilot.agents.terminal import create_terminal_agent
from basketpilot.run import build_bureau, load_local_env, seed_for


async def _input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


def _serve(initial: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    load_local_env()
    os.environ.setdefault("BP_USE_MOCK", "true")
    os.environ.setdefault("BP_MAILBOX", "false")
    os.environ.setdefault("BP_ALLOW_ORDERS", "false")
    bureau, agents = build_bureau(port=int(os.environ.get("BP_BUREAU_PORT", "8000")))

    def stop_bureau() -> None:
        if bureau._server.server is not None:
            bureau._server.server.should_exit = True
        for task in asyncio.all_tasks(loop):
            if getattr(task.get_coro(), "__qualname__", "").endswith("Bureau._schedule_registration"):
                task.cancel()

    client = create_terminal_agent(
        agents["orchestrator"].address,
        seed=seed_for("terminal"),
        port=8130,
        initial_text=initial,
        input_function=_input,
        stop_function=stop_bureau,
    )
    bureau.add(client)
    print("Starting local ACP conversation. Live orders are disabled; use /quit to stop.")
    bureau.run()


def main() -> None:
    initial = input("Paste grocery list (comma or newline separated): ").strip()
    if not initial:
        print("No list supplied.")
        return
    thread = threading.Thread(target=_serve, args=(initial,), daemon=False)
    thread.start()
    thread.join()


if __name__ == "__main__":
    main()
