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
    # Only a genuinely nested child belonging to an observed BUY LIMIT
    # OTO parent is even a candidate for coverage. An unrelated manual
    # sell stop must not justify declaring a PAPER position protected.
    # This still does NOT authenticate the parent against its original
    # approved stop price or prove that the broker will fill a gap.
    # A matching parent_order_id field alone is NOT proof of attachment.
    # Require the child to occur in that parent's actual nested broker legs,
    # with the same symbol, and a parent that has at least partially filled.
    # This remains diagnostic, NOT authentication against the journal's
    # originally approved stop price and order terms.
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
        protection = False
        for order in open_orders:
            if (getattr(order, "symbol", None) != symbol
                    or value(getattr(order, "side", None)) != "sell"
                    or value(getattr(order, "type", None)) not in PROTECTIVE_TYPES
                    or value(getattr(order, "status", None))
                       not in PROTECTIVE_ACTIVE_STATUSES
                    or value(getattr(order, "time_in_force", None)) != "gtc"):
                continue
            parent = observed_oto_parents.get(
                str(getattr(order, "parent_order_id", "")))
            if (parent is None
                    or getattr(parent, "symbol", None) != symbol
                    or value(getattr(parent, "status", None))
                       not in {"filled", "partially_filled"}
                    or not any(
                        str(getattr(leg, "id", "")) == str(getattr(order, "id", ""))
                        for leg in (getattr(parent, "legs", None) or [])
                    )):
                continue
            try:
                parent_qty = number(getattr(parent, "qty", None))
                remaining = (number(getattr(order, "qty", None))
                             - number(getattr(order, "filled_qty", None)))
                trigger = number(getattr(order, "stop_price", None))
            except TradeBlocked:
                continue
            if parent_qty >= qty and remaining >= qty and trigger > 0:
                protection = True
                break
        if not protection:
            uncovered.append(symbol)
            blockers.append("position_missing_verified_gtc_oto_stop")

    reasons = tuple(dict.fromkeys(blockers))
    return Reconciliation(
        consistent=not reasons, blockers=reasons,
        open_broker_orders=len(open_orders), open_broker_positions=len(positions),
        unresolved_journal_intents=len(unresolved),
        uncovered_symbols=tuple(sorted(set(uncovered))),
    )
