"""Persistent, policy-checked stdio client for Zepto's official MCP server."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack
from enum import StrEnum
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ToolPolicy(StrEnum):
    READ = "READ"
    WRITE_SAFE = "WRITE_SAFE"
    BLOCKED_ALWAYS = "BLOCKED_ALWAYS"
    MONEY = "MONEY"


TOOL_POLICY: dict[str, ToolPolicy] = {
    **dict.fromkeys((
        "search_products", "search_multiple_products", "get_product_details",
        "get_location_serviceability", "list_saved_addresses", "get_user_details",
        "view_cart", "get_payment_methods", "check_payment_status",
        "get_order_detail", "list_order_history", "get_past_order_items",
        "zepto_shop",
    ), ToolPolicy.READ),
    **dict.fromkeys(("update_cart", "select_saved_address", "select_store"), ToolPolicy.WRITE_SAFE),
    **dict.fromkeys((
        "create_upi_reserve_pay_order", "add_saved_address", "update_user_name",
        "update_drop_zone",
    ), ToolPolicy.BLOCKED_ALWAYS),
    **dict.fromkeys(("create_order", "create_online_payment_order", "create_wallet_order"), ToolPolicy.MONEY),
}
READ_TOOLS = frozenset(name for name, policy in TOOL_POLICY.items() if policy is ToolPolicy.READ)


class ZeptoError(Exception):
    """Safe, user-displayable MCP error without the raw server response."""


class AuthRequired(ZeptoError):
    pass


class StoreClosed(ZeptoError):
    pass


class Unserviceable(ZeptoError):
    pass


class ToolError(ZeptoError):
    pass


class AddressRequired(ToolError):
    pass


class Timeout(ZeptoError):
    pass


ToolTimeout = Timeout


class PolicyViolation(ZeptoError):
    pass


class _ConnectionFailure(Exception):
    pass


_SENSITIVE = re.compile(
    r"(?i)(\b(?:token|authorization|bearer|access[_ -]?token|refresh[_ -]?token|"
    r"session[_ -]?id|user[_ -]?id|address[_ -]?id|phone|mobile|contact[_ -]?number|"
    r"full[_ -]?name|contact[_ -]?name|\bname\b|\baddress\b|flat[_ -]?details|formatted[_ -]?address)\b"
    r"\s*[:=]\s*)([^,;\s]+|\"[^\"]*\"|'[^']*')"
)
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")


def redact(value: Any) -> str:
    """Remove common account and address identifiers before emitting log text."""
    text = str(value)
    text = _SENSITIVE.sub(r"\1[REDACTED]", text)
    return _PHONE.sub("[REDACTED_PHONE]", text)


def _safe_error(exc: BaseException) -> ZeptoError:
    message = redact(str(exc)).casefold()
    if any(key in message for key in (
        "unauthorized", "not authenticated", "authentication required", "login required",
        "token expired", "401", "403",
    )):
        return AuthRequired("Zepto login is required.")
    if "closed" in message:
        return StoreClosed("The Zepto store is closed.")
    if any(key in message for key in ("unserviceable", "not serviceable", "outside delivery")):
        return Unserviceable("Zepto does not serve the selected location.")
    if any(key in message for key in ("store not selected", "store context missing", "address required")):
        return AddressRequired("Select a Zepto address or store before continuing.")
    return ToolError("The Zepto request failed.")


def _unwrap_result(result: Any) -> Any:
    if getattr(result, "isError", False):
        message = " ".join(getattr(block, "text", "") for block in getattr(result, "content", ()))
        raise _safe_error(RuntimeError(message))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    blocks = getattr(result, "content", None) or ()
    texts = [getattr(block, "text", "") for block in blocks if getattr(block, "text", None)]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (ValueError, TypeError):
            return texts[0]
    if texts:
        return texts
    return None


class ZeptoMCPClient:
    """One persistent MCP session; reconnects at most once after a transport failure."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retry_backoff: float = 0.15,
        command: str = "npx",
        args: tuple[str, ...] = ("-y", "mcp-remote", "https://mcp.zepto.co.in/mcp"),
    ) -> None:
        self.timeout = timeout
        self.retry_backoff = retry_backoff
        self.command = command
        self.args = args
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._connect_lock = asyncio.Lock()
        self._reconnects = 0

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._session is not None:
                return
            stack = AsyncExitStack()
            try:
                streams = await stack.enter_async_context(stdio_client(
                    StdioServerParameters(command=self.command, args=list(self.args)),
                ))
                session = await stack.enter_async_context(ClientSession(*streams))
                await asyncio.wait_for(session.initialize(), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                await stack.aclose()
                raise ToolTimeout("Zepto MCP session initialization timed out.") from None
            except BaseException as exc:
                await stack.aclose()
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
                raise _safe_error(exc) from None
            self._stack, self._session = stack, session

    async def close(self) -> None:
        async with self._connect_lock:
            stack, self._stack = self._stack, None
            self._session = None
            if stack:
                await stack.aclose()

    async def __aenter__(self) -> "ZeptoMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def list_tools(self) -> tuple[Any, ...]:
        await self.connect()
        try:
            result = await asyncio.wait_for(self._session.list_tools(), timeout=self.timeout)  # type: ignore[union-attr]
            return tuple(result.tools)
        except ZeptoError:
            raise
        except asyncio.TimeoutError:
            raise ToolTimeout("Zepto tool discovery timed out.") from None
        except Exception as exc:
            raise _safe_error(exc) from None

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        return await self._call(tool, args or {}, internal_money=False)

    async def call_money(self, tool: str, args: dict[str, Any], *, authorized: bool) -> Any:
        if TOOL_POLICY.get(tool) is not ToolPolicy.MONEY or not authorized:
            raise PolicyViolation("Money tools require the guarded place_order path.")
        return await self._call(tool, args, internal_money=True)

    async def _call(self, tool: str, args: dict[str, Any], *, internal_money: bool) -> Any:
        policy = TOOL_POLICY.get(tool)
        if policy is None:
            raise PolicyViolation(f"Tool is not in the allowlist: {tool}")
        if policy is ToolPolicy.BLOCKED_ALWAYS:
            raise PolicyViolation(f"Tool is blocked by policy: {tool}")
        if policy is ToolPolicy.MONEY and not internal_money:
            raise PolicyViolation("Money tools may only be called through place_order.")
        if tool == "zepto_shop" and args.get("intent") not in {"search", "browse"}:
            raise PolicyViolation("zepto_shop is read-only here and accepts search or browse intent only.")

        is_read = policy is ToolPolicy.READ
        attempts = 2 if is_read else 1
        for attempt in range(attempts):
            try:
                await self.connect()
                result = await asyncio.wait_for(
                    self._session.call_tool(tool, arguments=args),  # type: ignore[union-attr]
                    timeout=self.timeout,
                )
                return _unwrap_result(result)
            except asyncio.TimeoutError:
                if is_read and attempt + 1 < attempts:
                    if self._reconnects < 1:
                        self._reconnects += 1
                        await self.close()
                    await asyncio.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise ToolTimeout("Zepto request timed out.") from None
            except ZeptoError as exc:
                if (
                    isinstance(exc, ToolError)
                    and is_read
                    and attempt + 1 < attempts
                ):
                    await asyncio.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise
            except Exception as exc:
                safe = _safe_error(exc)
                if isinstance(safe, AuthRequired):
                    raise safe from None
                if is_read and attempt + 1 < attempts and self._reconnects < 1:
                    self._reconnects += 1
                    await self.close()
                    await asyncio.sleep(self.retry_backoff)
                    continue
                logging.getLogger(__name__).warning("Zepto MCP call failed: %s", redact(safe))
                raise safe from None
        raise ToolError("The Zepto request failed.")
