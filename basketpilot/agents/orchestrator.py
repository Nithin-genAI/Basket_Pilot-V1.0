"""ACP gateway and persisted session workflow."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from typing import Any

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent, StartSessionContent,
    TextContent, chat_protocol_spec,
)

from basketpilot import messages
from basketpilot.agents.wire import model_data
from basketpilot.models import StoreStatus
from basketpilot.optimizer.normalize import paise_to_rupees
from basketpilot.session import SessionStore, transition


def _session_id(sender: str) -> str:
    return hashlib.sha256(sender.encode("utf-8")).hexdigest()[:24]


def _money(paise: int) -> str:
    return f"Rs {paise_to_rupees(paise)}"


def _format_plan(plan_msg: messages.CartPlan) -> str:
    lines = ["Optimized basket plan", "item | platform | product | qty | line price"]
    for row in plan_msg.assignments:
        lines.append(f"{row.item_id} | {row.platform} | {row.title} | {row.qty} | {_money(row.line_total)}")
    for platform, totals in sorted(plan_msg.per_platform.items()):
        lines.append(f"\n{platform} fees and total:")
        for key, amount in sorted(totals.items()):
            if key not in {"subtotal", "platform_total"}:
                lines.append(f"  {key}: {_money(amount)}")
        lines.append(f"  subtotal: {_money(totals.get('subtotal', 0))}")
        lines.append(f"  total: {_money(totals.get('platform_total', 0))}")
    lines.append(f"\nGrand total: {_money(plan_msg.grand_total)}")
    lines.append(f"Savings vs best single: {_money(plan_msg.savings_vs_best_single)}")
    lines.append(f"Savings vs naive with fees: {_money(plan_msg.savings_vs_naive)}")
    if plan_msg.baselines.get("best_single_platform") is not None:
        lines.append(f"Best single-platform total: {_money(plan_msg.baselines['best_single_platform'])}")
    if plan_msg.baselines.get("naive_with_fees") is not None:
        lines.append(f"Naive with fees: {_money(plan_msg.baselines['naive_with_fees'])}")
    if plan_msg.baselines.get("price_floor") is not None:
        lines.append(f"Fee-free price floor: {_money(plan_msg.baselines['price_floor'])}")
    lines.extend(f"Unfulfilled: {reason}" for reason in plan_msg.unfulfilled)
    lines.extend(f"Suggestion: {tip}" for tip in plan_msg.suggestions)
    lines.extend(plan_msg.rationale)
    if plan_msg.fees_estimated:
        lines.append("Some platform fees are estimated.")
    return "\n".join(lines)


def create_orchestrator_agent(
    *, seed: str, port: int, parser_address: str, search_addresses: dict[str, str],
    optimizer_address: str, checkout_address: str, store: SessionStore | None = None,
) -> Agent:
    agent = Agent(name="basketpilot", seed=seed, port=port, network="testnet")
    store = store or SessionStore()
    chat = Protocol(spec=chat_protocol_spec)
    sender_by_session: dict[str, str] = {}
    search_results: dict[str, dict[str, messages.SearchResult]] = defaultdict(dict)
    previews: dict[str, dict[str, messages.CheckoutPreview]] = defaultdict(dict)
    tokens_by_session: dict[str, dict[str, str]] = defaultdict(dict)
    order_results: dict[str, dict[str, messages.OrderResult]] = defaultdict(dict)
    address_by_session: dict[str, str] = {}
    expected_search: dict[str, set[str]] = {}
    expected_previews: dict[str, set[str]] = {}

    def load(session_id: str) -> dict[str, Any]:
        return store.get(session_id) or {"state": "IDLE", "items": [], "created_at": time.time()}

    def save(session_id: str, state: dict[str, Any]) -> None:
        state["last_active"] = time.time()
        store.put(session_id, state)

    async def reply(ctx: Context, recipient: str, text: str) -> None:
        await ctx.send(recipient, ChatMessage(content=[TextContent(type="text", text=text)]))

    async def begin_search(ctx: Context, session_id: str, state: dict[str, Any]) -> None:
        state["state"] = transition(state["state"], "address")
        state["address_selected"] = True
        save(session_id, state)
        address_by_session[session_id] = "local-selected"
        expected_search[session_id] = set(search_addresses)
        search_results[session_id].clear()
        await reply(ctx, sender_by_session[session_id], f"Searching {', '.join(sorted(search_addresses))} in parallel...")
        ctx.logger.info("Search fan-out session=%s platforms=%s", session_id, ",".join(sorted(search_addresses)))
        request = messages.SearchRequest(
            session_id=session_id,
            items=[messages.ItemData(**row) for row in state["items"]],
            address_id="local-selected" if os.environ.get("BP_USE_MOCK", "true").casefold() == "true" else None,
        )
        for platform, address in sorted(search_addresses.items()):
            await ctx.send(address, request)

    @chat.on_message(ChatMessage)
    async def handle_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        # ACP requires an acknowledgement for every inbound ChatMessage, including control messages.
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        sid = _session_id(sender)
        sender_by_session[sid] = sender
        control_start = any(isinstance(content, StartSessionContent) for content in msg.content)
        control_end = any(isinstance(content, EndSessionContent) for content in msg.content)
        if control_end:
            store.delete(sid)
            search_results.pop(sid, None)
            previews.pop(sid, None)
            tokens_by_session.pop(sid, None)
            order_results.pop(sid, None)
            return
        state = load(sid)
        if control_start:
            state = {"state": "IDLE", "items": [], "created_at": time.time()}
            save(sid, state)
        text = "\n".join(content.text for content in msg.content if isinstance(content, TextContent)).strip()
        if not text:
            return
        current = state.get("state", "IDLE")
        if current in {"IDLE", "FAILED", "PLACED"}:
            state = {"state": transition(current, "list"), "raw_list": text, "items": [], "created_at": time.time()}
            save(sid, state)
            await reply(ctx, sender, "Parsing your grocery list...")
            await ctx.send(parser_address, messages.ParseRequest(session_id=sid, text=text))
        elif current == "COLLECTING_LIST":
            state["raw_list"] = text
            save(sid, state)
            await reply(ctx, sender, "Rechecking the clarified list...")
            await ctx.send(parser_address, messages.ParseRequest(session_id=sid, text=text))
        elif current == "NEED_ADDRESS":
            await begin_search(ctx, sid, state)
        elif current == "AWAITING_CONFIRM":
            if text.casefold().strip(" .!\t") not in {"confirm", "confirm order", "confirm & place order"}:
                await reply(ctx, sender, "The preview is ready. Reply exactly `confirm` for the local dry-run, or start a new chat session to cancel.")
                return
            for platform, token in sorted(tokens_by_session[sid].items()):
                await ctx.send(checkout_address, messages.ConfirmRequest(session_id=sid, platform=platform, preview_token=token))
        else:
            await reply(ctx, sender, f"This session is {current.lower()}. Please wait for the current step to finish.")

    @chat.on_message(ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.debug("Chat acknowledgement received for %s", msg.acknowledged_msg_id)

    @agent.on_message(model=messages.ParsedList)
    async def parsed_list(ctx: Context, sender: str, msg: messages.ParsedList) -> None:
        sid = msg.session_id
        if sender != parser_address or sid not in sender_by_session:
            return
        state = load(sid)
        if msg.ambiguities:
            state["state"] = "COLLECTING_LIST"
            save(sid, state)
            question = "Please clarify these lines together, then resend the full list:\n" + "\n".join(f"- {a}" for a in msg.ambiguities)
            await reply(ctx, sender_by_session[sid], question)
            return
        if not msg.items:
            state["state"] = "FAILED"
            save(sid, state)
            await reply(ctx, sender_by_session[sid], "I couldn't identify grocery items. Please paste one item per line or separate items with commas.")
            return
        state["state"] = transition(state["state"], "parsed")
        state["items"] = [model_data(row) for row in msg.items]
        save(sid, state)
        if os.environ.get("BP_USE_MOCK", "true").casefold() == "true":
            prompt = f"Parsed {len(msg.items)} items. Which delivery address should I use? Type an address label; the local mock will not save address details."
        else:
            prompt = f"Parsed {len(msg.items)} items. Select your saved Zepto address first, or set ZEPTO_ADDRESS_ID in the environment, then type continue."
        await reply(ctx, sender_by_session[sid], prompt)

    @agent.on_message(model=messages.SearchResult)
    async def search_result(ctx: Context, sender: str, msg: messages.SearchResult) -> None:
        sid = msg.session_id
        if sid not in sender_by_session or search_addresses.get(msg.platform) != sender:
            ctx.logger.warning("Ignored unexpected search response from %s for %s (%s)", sender, sid, msg.platform)
            return
        ctx.logger.info("Search response collected: %s for %s", msg.platform, sid)
        search_results[sid][msg.platform] = msg
        if set(search_results[sid]) != expected_search.get(sid, set()):
            return
        state = load(sid)
        unavailable = [f"{row.platform}: {row.notice or row.store_status}" for row in search_results[sid].values() if row.store_status != StoreStatus.OK.value]
        if unavailable:
            await reply(ctx, sender_by_session[sid], "Platform notices: " + "; ".join(unavailable))
        await reply(ctx, sender_by_session[sid], "Search complete. Optimizing item prices, fees, and split penalties...")
        await ctx.send(optimizer_address, messages.OptimizeRequest(
            session_id=sid,
            items=[messages.ItemData(**row) for row in state["items"]],
            results=[search_results[sid][name] for name in sorted(search_results[sid])],
            multi_platform_penalty_paise=int(os.environ.get("BP_MULTI_PLATFORM_PENALTY_PAISE", "1000")),
        ))

    @agent.on_message(model=messages.CartPlan)
    async def optimized(ctx: Context, sender: str, msg: messages.CartPlan) -> None:
        sid = msg.session_id
        ctx.logger.info("Optimizer response arrived for %s with %d assignments", sid, len(msg.assignments))
        if sender != optimizer_address or sid not in sender_by_session:
            ctx.logger.warning("Ignored unexpected optimizer response from %s for %s", sender, sid)
            return
        state = load(sid)
        state["state"] = transition(state["state"], "planned")
        state["plan"] = model_data(msg)
        save(sid, state)
        await reply(ctx, sender_by_session[sid], _format_plan(msg))
        platforms = sorted({assignment.platform for assignment in msg.assignments})
        if not platforms:
            await reply(ctx, sender_by_session[sid], "No fulfillable items were found. No cart was built.")
            state["state"] = "FAILED"
            save(sid, state)
            return
        state["state"] = transition(state["state"], "build")
        save(sid, state)
        expected_previews[sid] = set(platforms)
        previews[sid].clear()
        tokens_by_session[sid].clear()
        await reply(ctx, sender_by_session[sid], "Building and validating the proposed carts...")
        ctx.logger.info("Checkout preview fan-out session=%s platforms=%s", sid, ",".join(platforms))
        for platform in platforms:
            await ctx.send(checkout_address, messages.CheckoutRequest(
                session_id=sid, action="build_preview", platform=platform,
                assignments=[row for row in msg.assignments if row.platform == platform],
                address_id=address_by_session.get(sid),
            ))

    @agent.on_message(model=messages.CheckoutPreview)
    async def checkout_preview(ctx: Context, sender: str, msg: messages.CheckoutPreview) -> None:
        sid = msg.session_id
        ctx.logger.info("Checkout preview arrived from %s for %s on %s", sender, sid, msg.platform)
        if sender != checkout_address or sid not in sender_by_session:
            ctx.logger.warning("Ignored unexpected checkout preview for %s", sid)
            return
        previews[sid][msg.platform] = msg
        tokens_by_session[sid][msg.platform] = msg.preview_token
        if set(previews[sid]) != expected_previews.get(sid, set()):
            ctx.logger.info("Waiting for additional checkout previews for %s", sid)
            return
        state = load(sid)
        state["state"] = transition(state["state"], "preview")
        save(sid, state)
        out = ["Validated cart preview (prices and fees are estimates when marked):"]
        for platform, preview in sorted(previews[sid].items()):
            out.append(f"\n{platform}")
            out.extend(f"  {line.title} x{line.qty}: {_money(line.line_total_paise)}" for line in preview.lines)
            out.extend(f"  {fee}: {_money(amount)}" for fee, amount in sorted(preview.fee_lines.items()))
            out.append(f"  subtotal: {_money(preview.subtotal_paise)}")
            out.append(f"  platform total: {_money(preview.total_paise)}")
            if preview.fees_estimated:
                out.append("  Fee breakdown is estimated.")
            if preview.validation_errors:
                out.extend(f"  Validation: {error}" for error in preview.validation_errors)
        out.append("\nNo order has been placed. Reply `confirm` for a dry-run confirmation (BP_ALLOW_ORDERS=false).")
        ctx.logger.info("Sending complete preview message for %s", sid)
        await reply(ctx, sender_by_session[sid], "\n".join(out))
        ctx.logger.info("Preview message delivered for %s", sid)
        state["state"] = "AWAITING_CONFIRM"
        save(sid, state)

    @agent.on_message(model=messages.OrderResult)
    async def order_result(ctx: Context, sender: str, msg: messages.OrderResult) -> None:
        if sender != checkout_address or msg.session_id not in sender_by_session:
            return
        state = load(msg.session_id)
        if state.get("state") != "AWAITING_CONFIRM":
            await reply(ctx, sender_by_session[msg.session_id], f"Cart build status for {msg.platform}: {msg.status}. {msg.error or ''}".strip())
            state["state"] = "FAILED"
            save(msg.session_id, state)
            return
        order_results[msg.session_id][msg.platform] = msg
        expected = set(tokens_by_session[msg.session_id])
        if not expected or set(order_results[msg.session_id]) != expected:
            return
        summary = ["Confirmation result:"]
        for platform, result in sorted(order_results[msg.session_id].items()):
            summary.append(f"{platform}: {result.status}. {result.error or ''}".strip())
        await reply(ctx, sender_by_session[msg.session_id], "\n".join(summary))
        if all(result.status == "DRY_RUN" for result in order_results[msg.session_id].values()):
            state["state"] = "IDLE"
            state.pop("plan", None)
            save(msg.session_id, state)
        order_results.pop(msg.session_id, None)

    agent.include(chat, publish_manifest=False)
    return agent
