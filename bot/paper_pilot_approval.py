"""Render the owner-only Neon approval call for an exact bounded PAPER order.

This module never connects to Neon or Alpaca. The returned SQL is a human
approval artifact: it must be reviewed and executed manually in an authenticated
Neon SQL session as the database owner. The executor's later dispatch function
remains the authoritative single-use proof that this approval exists and matches.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

from .live_quotes import LivePaperEntryPreview, build_live_protected_oto_request
from .paper_safety import TradeBlocked, money
from .paper_supervised_execution import _plan_from_quoted_preview, _request_sha256


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
    The owner function checks today's reserved intent and cap. A later
    dispatch function checks controls, single-use state and dispatch receipt.
    """
    plan = getattr(prepared, "plan", None)
    preview = getattr(prepared, "preview", None)
    if plan is None or not isinstance(preview, LivePaperEntryPreview):
        raise TradeBlocked("Prepared bounded PAPER order required")
    client_id = getattr(plan, "client_order_id", None)
    symbol = getattr(plan, "symbol", None)
    if not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id):
        raise TradeBlocked("Invalid prepared client order ID")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise TradeBlocked("Invalid prepared symbol")
    if (preview.client_order_id != client_id or preview.symbol != symbol
            or preview.order_submission_enabled is not False
            or preview.order_time_in_force != "gtc"
            or preview.order_class != "oto"
            or getattr(plan, "time_in_force", None) != "gtc"
            or getattr(plan, "order_class", None) != "oto"):
        raise TradeBlocked("Prepared order terms are inconsistent")
    shares = preview.shares
    if type(shares) is not int or shares < 1:
        raise TradeBlocked("Invalid prepared share quantity")
    limit_price = money(preview.buy_limit_usd)
    stop_price = money(preview.stop_usd)
    purchase_cap = money(preview.max_limit_purchase_usd)
    database_scale = Decimal("0.000001")
    if (limit_price != limit_price.quantize(database_scale)
            or stop_price != stop_price.quantize(database_scale)
            or limit_price <= 0 or stop_price <= 0 or stop_price >= limit_price
            or purchase_cap > money("5.00")
            or limit_price * shares > money("5.00")
            or purchase_cap != limit_price * shares
            or money(getattr(prepared, "fee_reserve_usd", None)) != money("0.50")):
        raise TradeBlocked("Prepared order violates bounded pilot limits")
    # Bind every human-visible term to both the reserved intent and the
    # actual SDK request. Never accept a caller-supplied digest as proof of
    # what is displayed: that permits approving one order and dispatching
    # another while retaining a syntactically valid hash.
    expected_plan = _plan_from_quoted_preview(preview)
    plan_terms = (
        plan.client_order_id, plan.symbol, plan.trading_day, plan.strategy,
        plan.whole_shares, money(plan.estimated_entry),
        money(plan.protective_stop), money(plan.estimated_cost),
        money(plan.risk_budget_usd), money(plan.estimated_risk_usd),
        plan.order_class, plan.time_in_force,
    )
    expected_terms = (
        expected_plan.client_order_id, expected_plan.symbol,
        expected_plan.trading_day, expected_plan.strategy,
        expected_plan.whole_shares, money(expected_plan.estimated_entry),
        money(expected_plan.protective_stop), money(expected_plan.estimated_cost),
        money(expected_plan.risk_budget_usd), money(expected_plan.estimated_risk_usd),
        expected_plan.order_class, expected_plan.time_in_force,
    )
    if plan_terms != expected_terms:
        raise TradeBlocked("Prepared plan differs from displayed order terms")
    from .paper_execution_journal import PaperExecutorJournal
    PaperExecutorJournal.validate_protected_intent(expected_plan)
    actual_request_hash = _request_sha256(
        build_live_protected_oto_request(preview))
    request_hash = getattr(prepared, "request_sha256", None)
    account_hash = getattr(prepared, "account_id_sha256", None)
    if (not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash)
            or not isinstance(account_hash, str) or not _HASH.fullmatch(account_hash)):
        raise TradeBlocked("Prepared order lacks exact request/account digest")
    if request_hash != actual_request_hash:
        raise TradeBlocked("Prepared request digest does not match Alpaca SDK order")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise TradeBlocked("Approval timestamp must be timezone-aware")
    expires = (now.astimezone(timezone.utc) + timedelta(seconds=120))
    expiry_sql = _sql_text(expires.isoformat(timespec="microseconds")) + "::timestamptz"
    return "\n".join((
        "-- OWNER ACTION: running this call approves this exact PAPER order for 120 seconds.",
        f"-- Exact order: BUY {shares} {symbol}, GTC OTO limit ${limit_price:.6f}, stop ${stop_price:.6f}.",
        "-- Verify the pinned PAPER account hash and safety state before running this owner-only call.",
        "-- This does not enable dispatch; database controls and the executor release gate still apply.",
        "SELECT public.paper_approve_bounded_pilot(",
        f"    {_sql_text(client_id)}, {_sql_text(symbol)}, {shares},",
        f"    {limit_price:.6f}, {stop_price:.6f},",
        f"    {_sql_text(request_hash)}, {_sql_text(account_hash)}, {expiry_sql}",
        ");",
    ))
