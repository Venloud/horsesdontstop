"""Strict read-only comparison of Alpaca PAPER positions/orders and Neon intents.

Never cancels, places or replaces an order. Missing, stale, truncated or
unknown broker information blocks readiness. This is a diagnostic check,
NOT execution approval or a real-time protective-stop watchdog.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from .paper_safety import TradeBlocked, money


OPEN_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled", "held", "stopped", "calculated",
})
# A stop-limit may fail to exit when an overnight gap skips its limit.
# DAY stops expire; held, pending-cancel, rejected and stopped states
# are NOT a proven active overnight protective stop.
PROTECTIVE_TYPES = frozenset({"stop"})
PROTECTIVE_ACTIVE_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled",
})
UNRESOLVED_INTENTS = frozenset({"reserved", "submitted", "accepted", "unknown"})


def value(item):
    return str(getattr(item, "value", item) or "").lower()


def number(value_):
    try:
        result = money(value_)
        if result < 0:
            raise TradeBlocked("Negative order quantity")
        return result
    except TradeBlocked:
        raise


def order_legs(orders: Iterable, max_depth: int = 4):
    """Flatten nested Alpaca multi-leg objects without counting child twice."""
    flat = []
    visited = {}

    def visit(order, depth, ancestors=frozenset()):
        if depth > max_depth:
            raise TradeBlocked("Order legs exceed safe nesting limit")
        key = value(getattr(order, "id", None))
        if not key:
            raise TradeBlocked("Broker order without id")
        if key in ancestors:
            raise TradeBlocked("Cyclic broker order tree")
        legs = getattr(order, "legs", None) or []
        if not isinstance(legs, (list, tuple)):
            raise TradeBlocked("Malformed broker child list")
        child_ids = tuple(sorted(str(value(getattr(x, "id", None))) for x in legs))
        if len(set(child_ids)) != len(child_ids):
            raise TradeBlocked("Duplicate nested broker child")
        fingerprint = tuple(value(getattr(order, name, None)) for name in (
            'symbol', 'side', 'type', 'qty', 'filled_qty', 'status',
            'time_in_force', 'order_class', 'client_order_id',
            'parent_order_id', 'stop_price', 'limit_price', 'expires_at',
            'filled_avg_price', 'filled_at')) + (child_ids,)
        if key in visited:
            if visited[key] != fingerprint:
                raise TradeBlocked('Conflicting broker order ID records')
        else:
            visited[key] = fingerprint
            flat.append(order)
        # Duplicate top-level snapshots must still verify every descendant;
        # otherwise a changed child/competing exit can hide behind deduplication.
        for leg in legs:
            visit(leg, depth + 1, ancestors | {key})

    for order in orders:
        visit(order, 0)
    return flat


def nested_parent_ids(orders: Iterable, max_depth: int = 4):
    """Derive parent edges from nested responses, never from a lone claim.

    Alpaca SDK Order has no parent_order_id field. Authenticated nesting is
    the relationship evidence; an optional explicit field must agree with it.
    A child attached to two parents is contradictory and fails closed.
    """
    parents = {}
    def visit(order, parent, depth):
        if depth > max_depth:
            raise TradeBlocked("Order legs exceed safe nesting limit")
        oid = value(getattr(order, "id", None))
        if not oid:
            raise TradeBlocked("Broker order without id")
        if parent is not None:
            claimed = value(getattr(order, "parent_order_id", None))
            if (claimed and claimed != parent or oid == parent
                    or oid in parents and parents[oid] != parent):
                raise TradeBlocked("Conflicting nested broker parent")
            parents[oid] = parent
        for leg in getattr(order, "legs", None) or []:
            visit(leg, oid, depth + 1)
    for order in orders:
        visit(order, None, 0)
    return parents


@dataclass(frozen=True)
class Reconciliation:
    consistent: bool
    blockers: tuple[str, ...]
    open_broker_orders: int
    open_broker_positions: int
    unresolved_journal_intents: int
    uncovered_symbols: tuple[str, ...]

    def to_dict(self):
        return {
            "consistent": self.consistent,
            "blockers": list(self.blockers),
            "open_broker_orders": self.open_broker_orders,
            "open_broker_positions": self.open_broker_positions,
            "unresolved_journal_intents": self.unresolved_journal_intents,
            "uncovered_symbols": list(self.uncovered_symbols),
        }


def reconcile_observation(
    positions: Sequence, orders: Sequence,
    journal_intents: Sequence[tuple],
    *, order_fetch_complete: bool,
    approved_stops: Sequence[tuple] | None = None,
) -> Reconciliation:
    """Require a known *flat* account and resolved durable journal.

    A journal tuple is (client_order_id, status, symbol, qty), as read
    directly from PostgreSQL. Do not use this report to mark unknown
    submissions canceled. A future worker needs single-ID Alpaca lookups
    before permitting any retry.
    """
    blockers = []
    if not order_fetch_complete:
        blockers.append("broker_order_history_incomplete")
    try:
        nested = order_legs(orders)
        parent_ids = nested_parent_ids(orders)
    except TradeBlocked:
        nested = []
        parent_ids = {}
        blockers.append("malformed_broker_order_tree")
    open_orders = [o for o in nested if value(getattr(o, "status", None)) in OPEN_STATUSES]
    if positions:
        blockers.append("open_broker_positions")
    if open_orders:
        blockers.append("open_broker_orders")
    ids = set()
    unresolved = []
    for row in journal_intents:
        if len(row) != 4:
            blockers.append("malformed_journal_intent")
            continue
        client_id, status, symbol, qty = row
        if not client_id or client_id in ids or not symbol:
            blockers.append("invalid_or_duplicate_journal_id")
        ids.add(client_id)
        if value(status) not in (
            "reserved", "submitted", "accepted", "filled",
            "rejected", "canceled", "unknown"
        ):
            blockers.append("unknown_journal_status")
        if value(status) in UNRESOLVED_INTENTS:
            unresolved.append(client_id)
        if number(qty) <= 0:
            blockers.append("invalid_journal_quantity")
    if unresolved:
        blockers.append("unresolved_order_intent_requires_broker_lookup")
    # A complete durable stop record must be read under the restricted
    # SELECT-only monitor role. Unresolved intents alone omit FILLED orders.
    # A parent/child with a convincing broker shape is not approved merely
    # because its broker-side identifiers match.
    durable_approvals = {}
    if positions and approved_stops is None:
        blockers.append("approved_stop_intents_not_observed")
    for record in approved_stops or ():
        if not isinstance(record, (tuple, list)) or len(record) not in (6, 7):
            blockers.append("invalid_approved_stop_intent")
            continue
        client_id, status, symbol, shares, stop, parent_id = record[:6]
        approved_tif = record[6] if len(record) == 7 else "day"
        try:
            quantity = number(shares)
            stop_price = number(stop)
        except TradeBlocked:
            blockers.append("invalid_approved_stop_intent")
            continue
        if (not isinstance(client_id, str) or not client_id
                or value(status) not in {"accepted", "filled"}
                or not isinstance(symbol, str) or not symbol
                or not parent_id or quantity <= 0 or stop_price <= 0
                or client_id in durable_approvals):
            blockers.append("invalid_approved_stop_intent")
            continue
        if approved_tif not in {"day", "gtc"}:
            blockers.append("invalid_approved_stop_intent")
            continue
        durable_approvals[client_id] = (
            str(parent_id), symbol, quantity, stop_price, approved_tif)

    by_id = {str(getattr(o, "id", "")): o for o in nested}
    known_clients = ids | set(durable_approvals)
    for order in open_orders:
        client_id = getattr(order, "client_order_id", None)
        parent = by_id.get(parent_ids.get(str(getattr(order, "id", ""))))
        if client_id not in known_clients and (
                parent is None or getattr(parent, "client_order_id", None) not in known_clients):
            blockers.append("open_order_not_in_durable_journal")

    # Only a genuinely nested child belonging to an observed BUY LIMIT
    # OTO parent is even a candidate for coverage. An unrelated manual
    # sell stop must not justify declaring a PAPER position protected.
    # Even matching durable intent cannot prove fills through market gaps.
    # A matching parent_order_id field alone is NOT proof of attachment.
    # Require the child to occur in that parent's actual nested broker legs,
    # with the same symbol, and a parent that has at least partially filled.
    # Durable stop-price, parent-ID and approved quantity checks follow.
    observed_oto_parents = {
        str(getattr(parent, "id", "")): parent
        for parent in nested
        if (value(getattr(parent, "order_class", None)) == "oto"
            and value(getattr(parent, "side", None)) == "buy"
            and value(getattr(parent, "type", None)) == "limit"
            and getattr(parent, "id", None))
    }
    uncovered = []
    for position in positions:
        symbol = getattr(position, "symbol", None)
        qty = number(getattr(position, "qty", None))
        if not symbol or qty <= 0:
            blockers.append("invalid_broker_position")
            continue
        # Any second active SELL exit (including LIMIT, MARKET or STOP_LIMIT)
        # can compete with the genuine protective STOP for the same shares.
        # Do not describe the position as having verified protection until
        # there is exactly one active exit and it is the durable OTO STOP.
        all_active_sell_exits = [
            o for o in open_orders
            if (getattr(o, "symbol", None) == symbol
                and value(getattr(o, "side", None)) == "sell"
                and value(getattr(o, "status", None)) in OPEN_STATUSES)
        ]
        all_active_sell_stops = [
            o for o in all_active_sell_exits
            if value(getattr(o, "type", None)) == "stop"
        ]
        if len(all_active_sell_stops) > 1:
            blockers.append("multiple_active_sell_stops_for_position")
        if len(all_active_sell_exits) > 1:
            blockers.append("multiple_active_sell_exits_for_position")
        valid_child_count = 0
        for order in open_orders:
            if (getattr(order, "symbol", None) != symbol
                    or value(getattr(order, "side", None)) != "sell"
                    or value(getattr(order, "type", None)) not in PROTECTIVE_TYPES
                    or value(getattr(order, "status", None))
                       not in PROTECTIVE_ACTIVE_STATUSES
                    or value(getattr(order, "time_in_force", None)) != "gtc"):
                continue
            expiry = getattr(order, "expires_at", None)
            if expiry is not None:
                try:
                    if isinstance(expiry, str):
                        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    if (not isinstance(expiry, datetime) or expiry.tzinfo is None
                            or expiry <= datetime.now(timezone.utc)):
                        continue
                except (ValueError, TypeError, OverflowError):
                    continue
            parent = observed_oto_parents.get(
                parent_ids.get(str(getattr(order, "id", "")), ""))
            durable = durable_approvals.get(
                str(getattr(parent, "client_order_id", ""))) if parent else None
            if (parent is None or durable is None
                    or durable[0] != str(getattr(parent, "id", ""))
                    or durable[1] != symbol
                    or getattr(parent, "symbol", None) != symbol
                    # Partial parent fills cannot prove Alpaca has
                    # activated a stop leg for an unfinished OTO entry.
                    or value(getattr(parent, "status", None)) != "filled"
                    or value(getattr(parent, "time_in_force", None)) != durable[4]
                    or not any(
                        str(getattr(leg, "id", "")) == str(getattr(order, "id", ""))
                        for leg in (getattr(parent, "legs", None) or [])
                    )):
                continue
            try:
                parent_qty = number(getattr(parent, "qty", None))
                parent_filled = number(getattr(parent, "filled_qty", None))
                stop_qty = number(getattr(order, "qty", None))
                stop_filled = number(getattr(order, "filled_qty", None))
                remaining = stop_qty - stop_filled
                trigger = number(getattr(order, "stop_price", None))
            except TradeBlocked:
                continue
            # Protective order may not be larger than the durable order,
            # and its trigger must equal the originally stored stop price.
            fill_consistent = (
                (value(getattr(order, "status", None)) == "partially_filled"
                 and 0 < stop_filled < stop_qty)
                or (value(getattr(order, "status", None))
                    in PROTECTIVE_ACTIVE_STATUSES - {"partially_filled"}
                    and stop_filled == 0))
            if (parent_qty == durable[2] and parent_filled == parent_qty
                    and stop_qty == parent_filled and fill_consistent
                    and 0 < remaining == qty and trigger == durable[3]):
                valid_child_count += 1
        if not (valid_child_count == 1 and len(all_active_sell_exits) == 1):
            uncovered.append(symbol)
            blockers.append("position_missing_verified_gtc_oto_stop")

    reasons = tuple(dict.fromkeys(blockers))
    return Reconciliation(
        consistent=not reasons, blockers=reasons,
        open_broker_orders=len(open_orders), open_broker_positions=len(positions),
        unresolved_journal_intents=len(unresolved),
        uncovered_symbols=tuple(sorted(set(uncovered))),
    )
