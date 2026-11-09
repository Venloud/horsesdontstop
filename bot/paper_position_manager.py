"""Risk-reducing PAPER lifecycle maintenance: stale entries, unprotected shares.

Only two broker writes exist here, both risk-reducing and both bounded:
  * cancel ONE stale/late OTO entry parent by broker order UUID (DELETE);
  * flatten ONE journaled position that lacks a verified GTC stop with a
    market SELL under the deterministic client ID ``<entry cid>-x``.
Positions that no durable journal intent explains are never touched (alert).
Writes are hard-gated by the same code release flag as entries; while it is
False the plan is computed and reported only (dry run). The kill switch does
NOT block maintenance: pausing must never prevent reducing risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .paper_broker_recovery import exit_client_order_id
from .paper_reconcile import OPEN_STATUSES, order_legs, reconcile_observation, value
from .paper_safety import TradeBlocked, money

NY = ZoneInfo("America/New_York")
STALE_ENTRY_SECONDS = 600            # unfilled entry older than 10 minutes
LATE_ENTRY_CUTOFF = (15, 45)         # cancel any open entry from 15:45 NY
FILL_GRACE_SECONDS = 120             # let an OTO stop leg activate after fill


@dataclass(frozen=True)
class MaintenanceAction:
    kind: str            # "cancel_entry" | "flatten"
    client_order_id: str
    symbol: str
    broker_order_id: str | None = None
    shares: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class MaintenancePlan:
    actions: tuple[MaintenanceAction, ...]
    unexplained_symbols: tuple[str, ...] = ()
    notes: tuple[str, ...] = dc_field(default_factory=tuple)


def _aware(ts):
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, datetime) and ts.tzinfo is not None:
        return ts
    return None


def plan_maintenance(*, positions, orders, unresolved, approved_stops, now) -> MaintenancePlan:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Maintenance requires a timezone-aware clock")
    journal_cids = {row[0]: row[2] for row in unresolved}
    journal_cids.update({row[0]: row[2] for row in approved_stops or ()})
    symbols_by_cid = dict(journal_cids)
    nested = order_legs(orders)
    parents = [o for o in nested
               if value(getattr(o, "side", None)) == "buy"
               and value(getattr(o, "order_class", None)) == "oto"
               and getattr(o, "client_order_id", None) in symbols_by_cid]
    actions, notes = [], []
    local = now.astimezone(NY)
    late = (local.hour, local.minute) >= LATE_ENTRY_CUTOFF
    open_parent_symbols = set()
    for parent in parents:
        status = value(getattr(parent, "status", None))
        if status not in OPEN_STATUSES:
            continue
        symbol = getattr(parent, "symbol", None)
        open_parent_symbols.add(symbol)
        submitted = _aware(getattr(parent, "submitted_at", None)
                           or getattr(parent, "created_at", None))
        age = (now - submitted).total_seconds() if submitted else None
        if late or age is None or age >= STALE_ENTRY_SECONDS:
            actions.append(MaintenanceAction(
                "cancel_entry", parent.client_order_id, symbol,
                broker_order_id=str(parent.id),
                reason="late_session_entry" if late else "stale_unfilled_entry"))
    rec = reconcile_observation(positions, orders, unresolved,
                                order_fetch_complete=len(orders) < 500,
                                approved_stops=approved_stops)
    cid_by_symbol = {}
    for cid, symbol in symbols_by_cid.items():
        cid_by_symbol.setdefault(symbol, []).append(cid)
    own_exits = {exit_client_order_id(cid) for cid in symbols_by_cid}
    pending_exit_symbols = {
        getattr(o, "symbol", None) for o in nested
        if getattr(o, "client_order_id", None) in own_exits
        and value(getattr(o, "status", None)) in OPEN_STATUSES}
    open_sell_symbols = {
        getattr(o, "symbol", None) for o in nested
        if value(getattr(o, "side", None)) == "sell"
        and value(getattr(o, "status", None)) in OPEN_STATUSES}
    unexplained = []
    for position in positions:
        symbol = getattr(position, "symbol", None)
        if symbol not in rec.uncovered_symbols:
            continue
        cids = sorted(cid_by_symbol.get(symbol, []))
        if not cids:
            unexplained.append(symbol)
            continue
        # Client IDs embed the trading day (hds-YYYYMMDD-...): newest wins.
        cids = cids[-1:]
        if symbol in pending_exit_symbols:
            # Our own flatten is already queued (e.g. after hours): wait for it.
            notes.append(f"{symbol}: bot exit order pending")
            continue
        if symbol in open_sell_symbols:
            # A live SELL (e.g. the stop leg) still holds these shares; a
            # second SELL could double-sell. Alert instead of flattening.
            notes.append(f"{symbol}: open SELL exists; not flattening")
            unexplained.append(symbol)
            continue
        if symbol in open_parent_symbols:
            notes.append(f"{symbol}: entry still open; cancel first, flatten after")
            continue
        parent = next((o for o in nested if getattr(o, "client_order_id", None) == cids[0]), None)
        filled_at = _aware(getattr(parent, "filled_at", None)) if parent is not None else None
        if (parent is not None and value(getattr(parent, "status", None)) == "filled"
                and (filled_at is None
                     or (now - filled_at).total_seconds() < FILL_GRACE_SECONDS)):
            notes.append(f"{symbol}: inside post-fill stop activation grace")
            continue
        qty = money(getattr(position, "qty", None))
        if qty <= 0:
            unexplained.append(symbol)
            continue
        actions.append(MaintenanceAction(
            "flatten", cids[0], symbol, shares=str(qty),
            reason="position_without_verified_gtc_stop"))
    return MaintenancePlan(tuple(actions), tuple(sorted(set(unexplained))), tuple(notes))


def execute_maintenance(trading, plan: MaintenancePlan, *, released: bool, sleeper=None):
    """Apply at most one cancel and one flatten per call. Never retries a POST."""
    import time
    from alpaca.common.exceptions import APIError
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    sleeper = sleeper or time.sleep
    done = []
    if not released:
        return tuple(("dry_run", a.kind, a.client_order_id) for a in plan.actions)
    cancels = [a for a in plan.actions if a.kind == "cancel_entry"][:1]
    flattens = [a for a in plan.actions if a.kind == "flatten"][:1]
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    for action in cancels:
        try:
            trading.cancel_order_by_id(action.broker_order_id)
        except APIError:
            raise TradeBlocked("Stale entry cancel rejected by broker") from None
        done.append(("canceled", action.kind, action.client_order_id))
        sleeper(3)  # allow the broker to settle legs before any flatten decision
    for action in flattens:
        exit_cid = exit_client_order_id(action.client_order_id)
        try:
            trading.get_order_by_client_id(exit_cid)
            done.append(("exit_already_exists", action.kind, action.client_order_id))
            continue
        except APIError as exc:
            if exc.status_code != 404:
                raise TradeBlocked("Exit-order lookup unavailable") from None
        positions = [p for p in trading.get_all_positions()
                     if getattr(p, "symbol", None) == action.symbol]
        if len(positions) != 1 or money(positions[0].qty) != money(action.shares):
            raise TradeBlocked("Position changed before flatten; re-evaluate next run")
        live = order_legs(trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, nested=True, limit=500)))
        if any(getattr(o, "symbol", None) == action.symbol
               and value(getattr(o, "side", None)) == "sell"
               and value(getattr(o, "status", None)) in OPEN_STATUSES for o in live):
            raise TradeBlocked("Open SELL appeared before flatten; not double-selling")
        try:
            trading.submit_order(order_data=MarketOrderRequest(
                symbol=action.symbol, qty=float(money(action.shares)),
                side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                client_order_id=exit_cid))
        except APIError:
            raise TradeBlocked("Flatten order rejected by broker") from None
        done.append(("flatten_submitted", action.kind, action.client_order_id))
    return tuple(done)
