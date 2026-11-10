"""Automatic PAPER position exits: trailing stop, time limit, operator close.

Exit rules (momentum_volume, preregistered baseline exits):
  * initial protective stop: the broker GTC OTO stop leg (unchanged, backstop);
  * trailing stop: exit when the bid falls to TRAIL_FRACTION (5%) below the
    highest price since the entry fill, once that level is above the broker stop;
  * time limit: exit at/after 15:45 NY on the MAX_HOLD_SESSIONS-th session held;
  * operator close: exit on explicit command.

One exit routine, never leaving shares unprotected:
  1. confirm exactly one live stop leg for the position; 2. cancel ONLY that
  leg by UUID and wait for Alpaca to confirm; 3. re-read the position;
  4. ONE market SELL of exactly the shares, client ID ``<entry cid>-x``;
  5. wait for the fill. If the SELL fails after the cancel, immediately place a
  replacement GTC stop at the durable stop price (client ID ``<entry cid>-s``)
  and raise for an urgent alert.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from .paper_broker_recovery import exit_client_order_id
from .paper_reconcile import OPEN_STATUSES, order_legs, value
from .paper_safety import TradeBlocked, money

NY = ZoneInfo("America/New_York")
TRAIL_FRACTION = Decimal("0.05")
MAX_HOLD_SESSIONS = 5
TIME_EXIT_AT = dtime(15, 45)
PROTECTIVE_ACTIVE = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding"})


@dataclass(frozen=True)
class ExitAction:
    client_order_id: str
    symbol: str
    shares: Decimal
    stop_leg_id: str
    durable_stop: Decimal
    reason: str          # trailing_stop | time_limit | operator_close
    detail: str = ""


def reprotect_client_order_id(entry_cid: str) -> str:
    return entry_cid + "-s"


def _floor_cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def bot_positions(*, positions, orders, approved_stops):
    """Positions explained by a filled journal intent with ONE live stop leg.

    Yields (position, intent_cid, durable_stop, parent, stop_leg).
    """
    nested = order_legs(orders)
    by_cid = {}
    for row in approved_stops or ():
        cid, status, symbol, shares, stop, parent_id = row[:6]
        if value(status) == "filled":
            by_cid.setdefault(symbol, []).append((cid, money(stop), str(parent_id)))
    for position in positions:
        symbol = getattr(position, "symbol", None)
        live = []
        for cid, stop, parent_id in by_cid.get(symbol, []):
            parent = next((o for o in nested if str(getattr(o, "id", "")) == parent_id), None)
            if parent is None:
                continue
            legs = [l for l in (getattr(parent, "legs", None) or [])
                    if value(getattr(l, "side", None)) == "sell"
                    and value(getattr(l, "type", None)) == "stop"
                    and value(getattr(l, "status", None)) in PROTECTIVE_ACTIVE]
            if len(legs) == 1:
                live.append((cid, stop, parent, legs[0]))
        # Same-symbol history (closed earlier trades) is ignored; exactly one
        # intent must hold the live stop for this position.
        if len(live) != 1:
            continue
        cid, stop, parent, leg = live[0]
        yield position, cid, stop, parent, leg


def plan_exits(*, positions, orders, approved_stops, now, market, operator_close=False):
    """market: {symbol: {"bid": Decimal, "high_water": Decimal, "sessions_held": int}}."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Exit planning requires a timezone-aware clock")
    local = now.astimezone(NY)
    actions = []
    for position, cid, stop, parent, leg in bot_positions(
            positions=positions, orders=orders, approved_stops=approved_stops):
        symbol = position.symbol
        shares = money(position.qty)
        if shares <= 0:
            continue
        base = dict(client_order_id=cid, symbol=symbol, shares=shares,
                    stop_leg_id=str(leg.id), durable_stop=stop)
        if operator_close:
            actions.append(ExitAction(reason="operator_close", **base))
            continue
        info = market.get(symbol)
        if not info:
            continue
        bid, high = money(info["bid"]), money(info["high_water"])
        trail = _floor_cents(high * (Decimal(1) - TRAIL_FRACTION))
        if bid > 0 and trail > stop and bid <= trail:
            actions.append(ExitAction(
                reason="trailing_stop",
                detail=f"bid {bid} <= trail {trail} (high {high})", **base))
            continue
        if info.get("sessions_held", 0) >= MAX_HOLD_SESSIONS and local.time() >= TIME_EXIT_AT:
            actions.append(ExitAction(
                reason="time_limit", detail=f"held {info['sessions_held']} sessions", **base))
    return tuple(actions)


def execute_exit(trading, action: ExitAction, *, released: bool, sleeper=None, polls=10):
    """Close ONE position via the guarded sequence. Returns a result dict."""
    import time
    from alpaca.common.exceptions import APIError
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
    sleeper = sleeper or time.sleep
    if not released:
        return {"result": "dry_run", "reason": action.reason, "client_order_id": action.client_order_id}
    exit_cid = exit_client_order_id(action.client_order_id)
    try:
        trading.get_order_by_client_id(exit_cid)
        return {"result": "exit_already_exists", "client_order_id": action.client_order_id}
    except APIError as exc:
        if exc.status_code != 404:
            raise TradeBlocked("Exit-order lookup unavailable") from None
    leg = trading.get_order_by_id(action.stop_leg_id)
    if value(getattr(leg, "status", None)) not in PROTECTIVE_ACTIVE:
        raise TradeBlocked("Stop leg no longer live; re-evaluate next run")
    try:
        trading.cancel_order_by_id(action.stop_leg_id)
    except APIError:
        raise TradeBlocked("Stop-leg cancel rejected; position still protected") from None
    canceled = False
    for _ in range(polls):
        sleeper(1)
        status = value(getattr(trading.get_order_by_id(action.stop_leg_id), "status", None))
        if status == "filled":
            return {"result": "stop_filled_during_exit", "client_order_id": action.client_order_id}
        if status == "canceled":
            canceled = True
            break
    if not canceled:
        raise TradeBlocked("Stop-leg cancel not confirmed; re-evaluate next run")
    held = [p for p in trading.get_all_positions() if getattr(p, "symbol", None) == action.symbol]
    if not held:
        return {"result": "already_flat", "client_order_id": action.client_order_id}
    shares = money(held[0].qty)
    if shares != action.shares:
        _reprotect(trading, action, shares)
        raise TradeBlocked("Position changed during exit; re-protected with GTC stop")
    try:
        trading.submit_order(order_data=MarketOrderRequest(
            symbol=action.symbol, qty=float(shares), side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=exit_cid))
    except Exception:
        _reprotect(trading, action, shares)
        raise TradeBlocked("Exit SELL failed; position re-protected with GTC stop") from None
    fill = None
    for _ in range(polls):
        sleeper(1)
        order = trading.get_order_by_client_id(exit_cid)
        if value(getattr(order, "status", None)) == "filled":
            fill = order
            break
    return {"result": "exit_filled" if fill else "exit_submitted",
            "reason": action.reason, "detail": action.detail,
            "client_order_id": action.client_order_id, "symbol": action.symbol,
            "shares": str(shares),
            "fill_price": str(getattr(fill, "filled_avg_price", "")) if fill else None}


def _reprotect(trading, action, shares):
    """Restore broker protection after a failed exit. Never raises silently."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import StopOrderRequest
    trading.submit_order(order_data=StopOrderRequest(
        symbol=action.symbol, qty=float(shares), side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, stop_price=float(action.durable_stop),
        client_order_id=reprotect_client_order_id(action.client_order_id)))


def market_snapshot(*, market_data, trading, symbol, filled_at, now):
    """Bid, high since fill (IEX minute bars) and sessions held, read only."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.requests import GetCalendarRequest
    quote = market_data.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))[symbol]
    bars = market_data.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=filled_at,
        feed=DataFeed.IEX)).data.get(symbol, [])
    highs = [money(b.high) for b in bars] + [money(getattr(quote, "bid_price", 0) or 0)]
    sessions = trading.get_calendar(GetCalendarRequest(
        start=filled_at.astimezone(NY).date(), end=now.astimezone(NY).date()))
    return {"bid": money(quote.bid_price), "high_water": max(highs),
            "sessions_held": len(sessions)}   # fill day counts as session 1
