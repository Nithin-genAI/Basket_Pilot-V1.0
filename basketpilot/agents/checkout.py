"""Checkout worker. Builds and validates previews; live spending remains disabled."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time

from uagents import Agent, Context

from basketpilot import messages
from basketpilot.agents.wire import assignment_from_wire
from basketpilot.models import CartLine
from basketpilot.optimizer.normalize import rupees_to_paise
from basketpilot.platforms.base import PlatformAdapter


def create_checkout_agent(adapters: dict[str, PlatformAdapter], seed: str, port: int) -> Agent:
    agent = Agent(name="bp-checkout", seed=seed, port=port, network="testnet")
    tokens: dict[str, tuple[str, float, str]] = {}

    @agent.on_message(model=messages.CheckoutRequest)
    async def checkout_request(ctx: Context, sender: str, msg: messages.CheckoutRequest) -> None:
        ctx.logger.info("Building checkout preview for %s on %s (reply to %s)", msg.session_id, msg.platform, sender)
        adapter = adapters.get(msg.platform)
        if adapter is None:
            return
        if msg.action != "build_preview":
            await ctx.send(sender, messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="REJECTED",
                error="Unsupported checkout action.",
            ))
            return
        if not msg.cart_write_confirmed:
            await ctx.send(sender, messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="BLOCKED",
                error="The user has not explicitly confirmed adding this basket to the cart.",
            ))
            return
        if os.environ.get("BP_USE_MOCK", "true").casefold() != "true" and os.environ.get("BP_ALLOW_CART_WRITES", "false").casefold() != "true":
            await ctx.send(sender, messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="BLOCKED",
                error="Live cart writes are disabled. Set BP_ALLOW_CART_WRITES=true only after reviewing the cart-write step.",
            ))
            return
        assignments = [assignment_from_wire(row) for row in msg.assignments]
        lines = tuple(CartLine(
            sku_id=row.sku_id, title=row.title, qty=row.qty,
            unit_price=row.unit_price / 100,
        ) for row in assignments)
        try:
            ctx.logger.info("Adding %d lines to adapter cart for %s", len(lines), msg.platform)
            snapshot = await adapter.add_to_cart(lines)
            ctx.logger.info("Cart snapshot received for %s", msg.platform)
            validation_errors = list(snapshot.validation_errors)
        except Exception as exc:
            ctx.logger.warning("Cart preview failed for %s (%s)", msg.platform, type(exc).__name__)
            await ctx.send(sender, messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="FAILED",
                error="Cart update or readback failed. Check the current Zepto cart before retrying; do not assume it stayed unchanged.",
            ))
            return

        total = rupees_to_paise(snapshot.total)
        subtotal = rupees_to_paise(snapshot.subtotal)
        fee_lines = {
            key: rupees_to_paise(value or 0)
            for key, value in (
                ("delivery", snapshot.fees.delivery), ("handling", snapshot.fees.handling),
                ("small_cart", snapshot.fees.small_cart), ("late_night", snapshot.fees.late_night),
                ("other", snapshot.fees.other),
            ) if value is not None
        }
        expiry = time.time() + 300
        cart_basis = json.dumps({
            "platform": msg.platform,
            "lines": [(line.sku_id, line.qty, rupees_to_paise(line.unit_price)) for line in snapshot.lines],
            "total": total,
            "address": msg.address_id or "",
        }, sort_keys=True)
        cart_hash = hashlib.sha256(cart_basis.encode()).hexdigest()
        token = secrets.token_urlsafe(24)
        tokens[token] = (cart_hash, expiry, msg.platform)
        ctx.logger.info("Sending preview response for %s", msg.platform)
        delivery = await ctx.send(sender, messages.CheckoutPreview(
            session_id=msg.session_id, platform=msg.platform,
            lines=[messages.PreviewLineData(
                sku_id=line.sku_id, title=line.title, qty=line.qty,
                unit_price_paise=rupees_to_paise(line.unit_price),
                line_total_paise=rupees_to_paise(line.unit_price * line.qty),
            ) for line in snapshot.lines],
            fee_lines=fee_lines, subtotal_paise=subtotal, total_paise=total,
            preview_token=token, expires_at=expiry,
            fees_estimated=snapshot.fees.fees_estimated, validation_errors=validation_errors,
        ))
        ctx.logger.info("Checkout preview delivery status: %s", getattr(delivery, "status", delivery))

    @agent.on_message(model=messages.ConfirmRequest)
    async def confirm_request(ctx: Context, sender: str, msg: messages.ConfirmRequest) -> None:
        token_record = tokens.pop(msg.preview_token, None)
        if token_record is None or token_record[1] < time.time() or token_record[2] != msg.platform:
            result = messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="REJECTED",
                error="Preview token is invalid, expired, or belongs to another platform.",
            )
        elif os.environ.get("BP_ALLOW_ORDERS", "false").casefold() != "true":
            result = messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="DRY_RUN",
                error="Order placement is disabled; no order was sent.",
            )
        else:
            result = messages.OrderResult(
                session_id=msg.session_id, platform=msg.platform, status="BLOCKED",
                error="Live order placement is not enabled in the local Step 3B runtime.",
            )
        await ctx.send(sender, result)

    return agent
