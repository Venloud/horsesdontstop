"""Strict read-only comparison of Alpaca PAPER positions/orders and Neon intents.

Never cancels, places or replaces an order. Missing, stale, truncated or
unknown broker information blocks readiness. This is a diagnostic check,
NOT execution approval or a real-time protective-stop watchdog.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from .paper_safety import TradeBlocked, money


OPEN_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled", "held", "stopped", "calculated",
})
PROTECTIVE_TYPES = frozenset({"stop", "stop_limit"})
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
    visited = set()

    def visit(order, depth):
        if depth > max_depth:
            raise TradeBlocked("Order legs exceed safe nesting limit")
        key = value(getattr(order, "id", None))
        if not key:
            raise TradeBlocked("Broker order without id")
        if key in visited:
            return
        visited.add(key)
        flat.append(order)
        for leg in getattr(order, "legs", None) or []:
            visit(leg, depth + 1)

    for order in orders:
        visit(order, 0)
    return flat


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
    except TradeBlocked:
        nested = []
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
    for order in open_orders:
        client_id = getattr(order, "client_order_id", None)
        if client_id not in ids and not getattr(order, "parent_order_id", None):
            blockers.append("open_order_not_in_durable_journal")
    uncovered = []
    for position in positions:
        symbol = getattr(position, "symbol", None)
        qty = number(getattr(position, "qty", None))
        if not symbol or qty == 0:
            blockers.append("invalid_broker_position")
            continue
        protection = any(
            getattr(order, "symbol", None) == symbol
            and value(getattr(order, "side", None)) == "sell"
            and value(getattr(order, "type", None)) in PROTECTIVE_TYPES
            and value(getattr(order, "status", None)) in OPEN_STATUSES
            and number(getattr(order, "qty", None)) >= qty
            for order in open_orders
        )
        if not protection:
            uncovered.append(symbol)
            blockers.append("position_missing_live_protective_stop")
    reasons = tuple(dict.fromkeys(blockers))
    return Reconciliation(
        consistent=not reasons, blockers=reasons,
        open_broker_orders=len(open_orders), open_broker_positions=len(positions),
        unresolved_journal_intents=len(unresolved),
        uncovered_symbols=tuple(sorted(set(uncovered))),
    )
