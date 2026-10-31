from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, StartSessionContent, TextContent,
    chat_protocol_spec,
)

from basketpilot import messages
from basketpilot.run import build_bureau, seed_for
from basketpilot.session import SessionStore, transition


class WireModelTests(unittest.TestCase):
    def test_local_mode_requires_environment_seeds_and_rejects_mailbox(self) -> None:
        with patch.dict(os.environ, {"BP_MAILBOX": "true"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "local-only"):
                build_bureau()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BP_SEED_ORCHESTRATOR"):
                seed_for("orchestrator")

    def test_expected_acp_version(self) -> None:
        self.assertEqual(chat_protocol_spec.name, "AgentChatProtocol")
        self.assertEqual(chat_protocol_spec.version, "0.3.0")

    def test_message_round_trips(self) -> None:
        original = messages.ParseRequest(session_id="s-1", text="1 kg rice")
        self.assertEqual(messages.ParseRequest.parse_raw(original.json()), original)

        plan = messages.CartPlan(
            session_id="s-1", assignments=[messages.AssignmentData(
                item_id="i1", platform="zepto-mock", sku_id="z-1", title="Rice 1kg",
                qty=1, unit_price=7500, line_total=7500,
            )], per_platform={"zepto-mock": {"subtotal": 7500, "delivery": 0, "platform_total": 7500}},
            grand_total=7500, baselines={"best_single_platform": 7500, "price_floor": 7500},
            savings_vs_best_single=0, savings_vs_naive=0, unfulfilled=[], rationale=[],
        )
        restored = messages.CartPlan.parse_raw(plan.json())
        self.assertEqual(restored, plan)

    def test_session_transitions_and_invalid_transition(self) -> None:
        self.assertEqual(transition("IDLE", "list"), "COLLECTING_LIST")
        self.assertEqual(transition("COLLECTING_LIST", "parsed"), "NEED_ADDRESS")
        self.assertEqual(transition("NEED_ADDRESS", "address"), "SEARCHING")
        self.assertEqual(transition("CART_BUILDING", "preview"), "AWAITING_CONFIRM")
        with self.assertRaises(ValueError):
            transition("IDLE", "confirm")

    def test_session_store_ttl_and_secret_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(str(Path(temp) / "sessions.sqlite3"), ttl_seconds=3600)
            store.put("session", {"state": "AWAITING_CONFIRM", "items": [], "address_id": "private", "preview_token": "secret"})
            loaded = store.get("session")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertNotIn("address_id", loaded)
            self.assertNotIn("preview_token", loaded)
            expired = SessionStore(str(Path(temp) / "expired.sqlite3"), ttl_seconds=0)
            expired.put("old", {"state": "IDLE"})
            self.assertIsNone(expired.get("old"))


class LocalBureauTests(unittest.IsolatedAsyncioTestCase):
    async def test_scripted_acp_conversation_acks_and_dry_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "BP_USE_MOCK": "true",
            "BP_MAILBOX": "false",
            "BP_ALLOW_ORDERS": "false",
            "BP_SESSION_DB": str(Path(temp) / "sessions.sqlite3"),
            "BP_SEED_ORCHESTRATOR": "test-orchestrator-seed-basketpilot-3b",
            "BP_SEED_PARSER": "test-parser-seed-basketpilot-3b",
            "BP_SEED_OPTIMIZER": "test-optimizer-seed-basketpilot-3b",
            "BP_SEED_CHECKOUT": "test-checkout-seed-basketpilot-3b",
            "BP_SEED_SEARCH_ZEPTO_MOCK": "test-zepto-seed-basketpilot-3b",
            "BP_SEED_SEARCH_INSTAMART_MOCK": "test-instamart-seed-basketpilot-3b",
        }, clear=False):
            done = threading.Event()
            shared: dict[str, object] = {"replies": [], "acks": [], "interaction": {"address": False, "confirmed": False}, "error": None}
            refs: dict[str, object] = {}

            def run_local_bureau() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    bureau, components = build_bureau(port=8189, session_store=SessionStore(str(Path(temp) / "sessions.sqlite3")))
                    refs["bureau"] = bureau
                    orchestrator = components["orchestrator"]
                    client = Agent(name="test-acp-client", seed="test-client-seed-basketpilot-3b", port=8190, network="testnet")
                    chat = Protocol(spec=chat_protocol_spec)

                    @client.on_event("startup")
                    async def start(ctx: Context) -> None:
                        await ctx.send(orchestrator.address, ChatMessage(content=[
                            StartSessionContent(type="start-session"),
                            TextContent(type="text", text="1 kg apple, 1 L milk"),
                        ]))

                    @chat.on_message(ChatMessage)
                    async def receive(ctx: Context, sender: str, msg: ChatMessage) -> None:
                        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
                        text = "\n".join(block.text for block in msg.content if isinstance(block, TextContent))
                        shared["replies"].append(text)
                        lowered = text.casefold()
                        interaction = shared["interaction"]
                        if "which delivery address" in lowered and not interaction["address"]:
                            interaction["address"] = True
                            await ctx.send(orchestrator.address, ChatMessage(content=[TextContent(type="text", text="home")]))
                        elif "reply `confirm`" in lowered and not interaction["confirmed"]:
                            interaction["confirmed"] = True
                            await ctx.send(orchestrator.address, ChatMessage(content=[TextContent(type="text", text="confirm")]))
                        elif "dry_run" in lowered:
                            done.set()
                            if bureau._server.server is not None:
                                bureau._server.server.should_exit = True
                            for task in asyncio.all_tasks():
                                if getattr(task.get_coro(), "__qualname__", "").endswith("Bureau._schedule_registration"):
                                    task.cancel()

                    @chat.on_message(ChatAcknowledgement)
                    async def ack_received(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
                        shared["acks"].append(str(msg.acknowledged_msg_id))

                    client.include(chat, publish_manifest=False)
                    bureau.add(client)
                    bureau.run()
                except Exception as exc:
                    shared["error"] = repr(exc)
                    done.set()
                finally:
                    loop.close()

            thread = threading.Thread(target=run_local_bureau, daemon=True)
            thread.start()
            await asyncio.to_thread(done.wait, 20)
            if thread.is_alive() and "bureau" in refs:
                bureau = refs["bureau"]
                loop = bureau._loop
                def stop() -> None:
                    if bureau._server.server is not None:
                        bureau._server.server.should_exit = True
                    for task in asyncio.all_tasks(loop):
                        if getattr(task.get_coro(), "__qualname__", "").endswith("Bureau._schedule_registration"):
                            task.cancel()
                loop.call_soon_threadsafe(stop)
            await asyncio.to_thread(thread.join, 8)
            self.assertFalse(thread.is_alive(), "local Bureau did not shut down")
            self.assertIsNone(shared["error"], shared["error"])
            interaction = shared["interaction"]
            replies = shared["replies"]
            self.assertTrue(interaction["address"])
            self.assertTrue(interaction["confirmed"])
            self.assertTrue(any("Optimized basket plan" in text for text in replies))
            self.assertTrue(any("Validated cart preview" in text for text in replies))
            self.assertTrue(any("DRY_RUN" in text for text in replies))
            # The orchestrator acknowledges every client ChatMessage, including ACP session control.
            self.assertGreaterEqual(len(shared["acks"]), 3)


if __name__ == "__main__":
    unittest.main()
