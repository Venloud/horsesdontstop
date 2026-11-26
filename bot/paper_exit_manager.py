"""Automatic PAPER position exits: trailing stop, time limit, operator close.

Exit rules (momentum_volume, preregistered baseline exits):
  * initial protective stop: the broker GTC OTO stop leg (unchanged, backstop);
  * take profit: exit when the bid reaches TAKE_PROFIT_FRACTION (6%) above the
    entry fill price (owner decision 2026-11-17, about 2:1 vs the ~3% stop);
  * trailing stop: exit when the bid falls to TRAIL_FRACTION (5%) below the
    highest price since the entry fill, once that level is above the broker stop;
  * time limit: exit at/after 15:45 NY on the MAX_HOLD_SESSIONS-th session held,
    or at the first regular-session run after that deadline was missed;
  * operator close: exit on explicit command.

Automatic exits are planned only while the regular session is open, so the
stop is never cancelled for a market SELL that would sit queued overnight.

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
TAKE_PROFIT_FRACTION = Decimal("0.06")
MAX_HOLD_SESSIONS = 5
# A price that disagrees with the broker's own position mark (or the latest
# quote with the latest trade) by more than this is treated as bad data:
# price-based exits are skipped for that run (incident 2026-11-18: IEX
# after-hours bid 119.62 while WMT traded 125.10-127.70).
PRICE_SANITY_FRACTION = Decimal("0.03")
TIME_EXIT_AT = dtime(15, 45)
PROTECTIVE_ACTIVE = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding"})
CANCEL_CONFIRM_POLLS = 45
EXIT_WORKING = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding",
                          "partially_filled", "pending_cancel", "pending_replace",
                          "replaced", "held", "calculated", "done_for_day"})


EARLY_CLOSE_MARGIN_MINUTES = 15


def time_exit_cutoff(session_close=None):
    """15:45 NY, or 15 minutes before today's close on early-close days
    (e.g. 13:00 day after Thanksgiving -> 12:45). session_close is the broker
    calendar's close for today (datetime or time, NY local) or None."""
    if session_close is None:
        return TIME_EXIT_AT
    if isinstance(session_close, datetime):
        session_close = (session_close.astimezone(NY) if session_close.tzinfo
                         else session_close).time()
    minutes = session_close.hour * 60 + session_close.minute - EARLY_CLOSE_MARGIN_MINUTES
    if minutes <= 0:
        return TIME_EXIT_AT
    return min(TIME_EXIT_AT, dtime(minutes // 60, minutes % 60))


@dataclass(frozen=True)
class ExitAction:
    client_order_id: str
    symbol: str
    shares: Decimal
    stop_leg_id: str
    durable_stop: Decimal
    reason: str          # take_profit | trailing_stop | time_limit | operator_close
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
            if not legs:
                # After a failed exit the stop is a standalone GTC order "<cid>-s".
                legs = [o for o in nested
                        if getattr(o, "client_order_id", None) == reprotect_client_order_id(cid)
                        and value(getattr(o, "status", None)) in PROTECTIVE_ACTIVE]
            if len(legs) == 1:
                live.append((cid, stop, parent, legs[0]))
        # Same-symbol history (closed earlier trades) is ignored; exactly one
        # intent must hold the live stop for this position.
        if len(live) == 1:
            cid, stop, parent, leg = live[0]
            yield position, cid, stop, parent, leg
            continue
        if live:
            continue
        # Incident 2026-11-20: an exit's stop-leg cancel confirmed only after the
        # run gave up, leaving shares held with NO stop. Recover only the newest
        # filled intent (older same-symbol trades are closed history) when its
        # stop leg is canceled, it fully explains the position, and no
        # replacement stop exists. The caller must sell or re-protect it.
        latest = None
        for cid, stop, parent_id in by_cid.get(symbol, []):
            parent = next((o for o in nested if str(getattr(o, "id", "")) == parent_id), None)
            if parent is None or getattr(parent, "filled_at", None) is None:
                continue
            if latest is None or parent.filled_at > latest[2].filled_at:
                latest = (cid, stop, parent)
        if latest is None:
            continue
        cid, stop, parent = latest
        stops = [l for l in (getattr(parent, "legs", None) or [])
                 if value(getattr(l, "side", None)) == "sell"
                 and value(getattr(l, "type", None)) == "stop"]
        if (len(stops) == 1 and value(getattr(stops[0], "status", None)) == "canceled"
                and money(getattr(parent, "filled_qty", 0) or 0) == money(position.qty)):
            yield position, cid, stop, parent, stops[0]


def _ceil_cents(x: Decimal) -> Decimal:
    from decimal import ROUND_UP
    return x.quantize(Decimal("0.01"), rounding=ROUND_UP)


def position_assessment(*, entry, bid, high_water, durable_stop, sessions_held):
    """Plain gain/loss picture of one position against every exit rule."""
    entry, bid, high = money(entry), money(bid), money(high_water)
    target = _ceil_cents(entry * (Decimal(1) + TAKE_PROFIT_FRACTION)) if entry > 0 else None
    trail = _floor_cents(high * (Decimal(1) - TRAIL_FRACTION))
    pnl = ((bid - entry) / entry * 100).quantize(Decimal("0.01")) if entry > 0 and bid > 0 else None
    return {"entry": str(entry), "bid": str(bid), "pnl_pct": None if pnl is None else str(pnl),
            "high_water": str(high), "take_profit_at": None if target is None else str(target),
            "trail_at": str(trail), "trail_active": trail > durable_stop,
            "stop_at": str(durable_stop), "sessions_held": sessions_held,
            "max_sessions": MAX_HOLD_SESSIONS}


def plan_exits(*, positions, orders, approved_stops, now, market, market_open,
               operator_close=False):
    """market: {symbol: {"bid", "high_water", "sessions_held", optional "entry"}}.

    market_open must be the broker clock's is_open. Nothing is planned while
    the regular session is closed (an overdue exit waits for the next open).
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Exit planning requires a timezone-aware clock")
    if market_open is not True:
        return ()
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
        if info.get("price_ok") is False:
            bid = Decimal(0)          # unreliable price: only the time limit may act
        entry = money(info.get("entry") or getattr(parent, "filled_avg_price", None) or 0)
        if entry > 0 and bid > 0:
            target = _ceil_cents(entry * (Decimal(1) + TAKE_PROFIT_FRACTION))
            if bid >= target:
                actions.append(ExitAction(
                    reason="take_profit",
                    detail=f"bid {bid} >= target {target} (entry {entry})", **base))
                continue
        trail = _floor_cents(high * (Decimal(1) - TRAIL_FRACTION))
        if bid > 0 and trail > stop and bid <= trail:
            actions.append(ExitAction(
                reason="trailing_stop",
                detail=f"bid {bid} <= trail {trail} (high {high})", **base))
            continue
        held = info.get("sessions_held", 0)
        if held > MAX_HOLD_SESSIONS or (held == MAX_HOLD_SESSIONS
                                        and local.time() >= time_exit_cutoff(info.get("session_close"))):
            overdue = " (overdue)" if held > MAX_HOLD_SESSIONS else ""
            actions.append(ExitAction(
                reason="time_limit", detail=f"held {held} sessions{overdue}", **base))
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
        existing = trading.get_order_by_client_id(exit_cid)
    except APIError as exc:
        if exc.status_code != 404:
            raise TradeBlocked("Exit-order lookup unavailable") from None
        existing = None
    if existing is not None:
        status = value(getattr(existing, "status", None))
        if status == "filled":
            return {"result": "exit_already_exists", "client_order_id": action.client_order_id}
        if status in EXIT_WORKING:
            # Pending or partially filled: never cancel or re-sell; wait for it.
            return {"result": "exit_pending", "status": status,
                    "filled_qty": str(getattr(existing, "filled_qty", "")),
                    "client_order_id": action.client_order_id}
        # canceled/expired/rejected exit while shares remain: the client ID is
        # spent, so never re-sell automatically; make sure shares are protected.
        _ensure_protected(trading, action)
        raise TradeBlocked(f"Earlier exit ended {status}; position protected, operator review needed")
    leg = trading.get_order_by_id(action.stop_leg_id)
    leg_status = value(getattr(leg, "status", None))
    if leg_status == "canceled":
        # An earlier run's cancel landed after it stopped waiting (2026-11-20):
        # the shares are unprotected, so finish the exit now.
        pass
    elif leg_status not in PROTECTIVE_ACTIVE:
        raise TradeBlocked("Stop leg no longer live; re-evaluate next run")
    else:
        try:
            trading.cancel_order_by_id(action.stop_leg_id)
        except APIError:
            raise TradeBlocked("Stop-leg cancel rejected; position still protected") from None
        canceled = False
        # Alpaca PAPER took ~20 s to confirm a cancel at the open; wait longer
        # than that. If it still is not confirmed, the next run recovers.
        for _ in range(max(polls, CANCEL_CONFIRM_POLLS)):
            sleeper(1)
            status = value(getattr(trading.get_order_by_id(action.stop_leg_id), "status", None))
            if status == "filled":
                return {"result": "stop_filled_during_exit",
                        "client_order_id": action.client_order_id}
            if status == "canceled":
                canceled = True
                break
        if not canceled:
            raise TradeBlocked("Stop-leg cancel not confirmed; next run sells or re-protects")
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
        # Ambiguous: the SELL may have reached Alpaca. Look before re-protecting,
        # so a live SELL never races a fresh stop (double sale / short).
        try:
            landed = trading.get_order_by_client_id(exit_cid)
        except APIError as exc:
            if exc.status_code != 404:
                raise TradeBlocked("Exit SELL outcome unknown; NOT re-protected, urgent review") from None
            landed = None
        except Exception:
            raise TradeBlocked("Exit SELL outcome unknown; NOT re-protected, urgent review") from None
        if landed is None or value(getattr(landed, "status", None)) in ("rejected", "canceled", "expired"):
            _reprotect(trading, action, shares)
            raise TradeBlocked("Exit SELL failed; position re-protected with GTC stop") from None
        return {"result": "exit_submitted", "reason": action.reason, "detail": action.detail,
                "client_order_id": action.client_order_id, "symbol": action.symbol,
                "shares": str(shares), "fill_price": None,
                "note": "recovered after ambiguous submit"}
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


def _ensure_protected(trading, action):
    """Place the re-protect stop only if shares are held and no stop is live."""
    from alpaca.common.exceptions import APIError
    held = [p for p in trading.get_all_positions() if getattr(p, "symbol", None) == action.symbol]
    if not held:
        return
    try:
        leg = trading.get_order_by_id(action.stop_leg_id)
        if value(getattr(leg, "status", None)) in PROTECTIVE_ACTIVE:
            return
    except APIError:
        pass
    try:
        existing = trading.get_order_by_client_id(reprotect_client_order_id(action.client_order_id))
        if value(getattr(existing, "status", None)) in PROTECTIVE_ACTIVE:
            return
    except APIError as exc:
        if exc.status_code != 404:
            raise TradeBlocked("Protection lookup unavailable; urgent review") from None
    _reprotect(trading, action, money(held[0].qty))


def _reprotect(trading, action, shares):
    """Restore broker protection after a failed exit. Never raises silently."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import StopOrderRequest
    trading.submit_order(order_data=StopOrderRequest(
        symbol=action.symbol, qty=float(shares), side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, stop_price=float(action.durable_stop),
        client_order_id=reprotect_client_order_id(action.client_order_id)))


def _disagree(a: Decimal, b: Decimal) -> bool:
    return a > 0 and b > 0 and abs(a - b) / b > PRICE_SANITY_FRACTION


def market_snapshot(*, market_data, trading, symbol, filled_at, now, position_price=None):
    """Price, high since fill (IEX minute bars) and sessions held, read only.

    "bid" is the LAST TRADE price (a real print), not the raw quote, which can
    be stale or very wide outside regular hours. "price_ok" is False when the
    trade disagrees with the broker's position mark or the quote bid by more
    than PRICE_SANITY_FRACTION; plan_exits then skips price-based exits.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import (
        StockBarsRequest, StockLatestQuoteRequest, StockLatestTradeRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.requests import GetCalendarRequest
    trade = market_data.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))[symbol]
    quote = market_data.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))[symbol]
    bars = market_data.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=filled_at,
        feed=DataFeed.IEX)).data.get(symbol, [])
    price = money(getattr(trade, "price", 0) or 0)
    quote_bid = money(getattr(quote, "bid_price", 0) or 0)
    mark = money(position_price or 0)
    problems = []
    if price <= 0:
        problems.append("no last trade")
    if _disagree(price, mark):
        problems.append(f"trade {price} vs position mark {mark}")
    note = (f"quote bid {quote_bid} ignored (vs trade {price})"
            if quote_bid > 0 and _disagree(quote_bid, price) else None)
    # High water from real trades only (bars + last trade), never the quote.
    highs = [money(b.high) for b in bars] + ([price] if price > 0 else [])
    sessions = trading.get_calendar(GetCalendarRequest(
        start=filled_at.astimezone(NY).date(), end=now.astimezone(NY).date()))
    # The calendar lists trading days only, so weekends/holidays never count.
    today = now.astimezone(NY).date()
    close = None
    for day in sessions:
        d = getattr(day, "date", None)
        if (d.date() if isinstance(d, datetime) else d) == today:
            close = getattr(day, "close", None)
    return {"session_close": close, "bid": price, "quote_bid": quote_bid, "position_mark": mark,
            "price_ok": not problems, "price_problem": "; ".join(problems) or None,
            "quote_note": note,
            "high_water": max(highs) if highs else Decimal(0),
            "sessions_held": len(sessions)}   # fill day counts as session 1
