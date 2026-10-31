"""Render the owner-only Neon approval call for an exact bounded PAPER order.

This module never connects to Neon or Alpaca. The returned SQL is a human
approval artifact: it must be reviewed and executed manually in an authenticated
Neon SQL session as the database owner. The executor's later dispatch function
remains the authoritative single-use proof that this approval exists and matches.
"""
from datetime import datetime, timedelta, timezone
import re

from .paper_safety import TradeBlocked, money


_CLIENT_ID = re.compile(r"hds-[0-9]{8}-[A-Z]{1,5}-[0-9a-f]{12}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Z]{1,5}\Z")


def _sql_text(value: str) -> str:
    """Quote a validated text value as a PostgreSQL string literal."""
    if not isinstance(value, str) or "\x00" in value:
        raise TradeBlocked("Invalid approval SQL text value")
    return "'" + value.replace("'", "''") + "'"


def render_owner_approval_sql(prepared, *, now: datetime | None = None) -> str:
    """Return an exact, expiring owner approval call; never execute it.

    The only interpolated user-controlled strings are strictly validated IDs,
    symbols, and SHA-256 digests. Decimal values are normalized before render.
    The database function rechecks the reserved intent, trading date, cap,
    single-use state, kill switch, submissions flag, and dispatch receipt.
    """
    plan = getattr(prepared, "plan", None)
    preview = getattr(prepared, "preview", None)
    if plan is None or preview is None:
        raise TradeBlocked("Prepared bounded PAPER order required")
    client_id = getattr(plan, "client_order_id", None)
    symbol = getattr(plan, "symbol", None)
    if not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id):
        raise TradeBlocked("Invalid prepared client order ID")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise TradeBlocked("Invalid prepared symbol")
    if (getattr(preview, "client_order_id", None) != client_id
            or getattr(preview, "symbol", None) != symbol
            or getattr(plan, "time_in_force", None) != "gtc"
            or getattr(plan, "order_class", None) != "oto"):
        raise TradeBlocked("Prepared order terms are inconsistent")
    shares = getattr(plan, "whole_shares", None)
    if type(shares) is not int or shares < 1:
        raise TradeBlocked("Invalid prepared share quantity")
    limit_price = money(getattr(preview, "buy_limit_usd", None))
    stop_price = money(getattr(preview, "stop_usd", None))
    purchase_cap = money(getattr(preview, "max_limit_purchase_usd", None))
    if (limit_price <= 0 or stop_price <= 0 or stop_price >= limit_price
            or purchase_cap > money("5.00")
            or limit_price * shares > money("5.00")
            or money(getattr(prepared, "fee_reserve_usd", None)) != money("0.50")):
        raise TradeBlocked("Prepared order violates bounded pilot limits")
    request_hash = getattr(prepared, "request_sha256", None)
    account_hash = getattr(prepared, "account_id_sha256", None)
    if (not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash)
            or not isinstance(account_hash, str) or not _HASH.fullmatch(account_hash)):
        raise TradeBlocked("Prepared order lacks exact request/account digest")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise TradeBlocked("Approval timestamp must be timezone-aware")
    expires = (now.astimezone(timezone.utc) + timedelta(seconds=120))
    expiry_sql = _sql_text(expires.isoformat(timespec="microseconds")) + "::timestamptz"
    return "\n".join((
        "-- OWNER ACTION: running this call approves this exact PAPER order for 120 seconds.",
        "-- Verify account, symbol, side, quantity, prices, GTC OTO stop and PAPER-only state first.",
        "-- This does not enable dispatch; database controls and the executor release gate still apply.",
        "SELECT public.paper_approve_bounded_pilot(",
        f"    {_sql_text(client_id)}, {_sql_text(symbol)}, {shares},",
        f"    {limit_price:.6f}, {stop_price:.6f},",
        f"    {_sql_text(request_hash)}, {_sql_text(account_hash)}, {expiry_sql}",
        ");",
    ))
