"""Alpaca PAPER-only broker order recovery + linked-stop verification.

READ ONLY. No submit_order(), cancel_order(), replace_order() calls here.
Never classify an unverified stop or unknown broker outcome as safe,
even if an OTO order was initially accepted.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional

from .paper_safety import PAPER_ENDPOINT, PaperOrderPlan, TradeBlocked, money


ENTRY_OPEN = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding", "held",
})
ENTRY_TERMINAL_UNFILLED = frozenset({
    "rejected", "canceled", "expired",
})
PROTECTIVE_ACTIVE = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled",
})
EXIT_FINISHED = frozenset({"filled"})
VALID_CHILD_TYPES = frozenset({"stop"})  # stop LIMIT could fail to exit in a gap


def field(item, name):
    raw = getattr(item, name, None)
    return str(getattr(raw, "value", raw) or "").lower()


def quantity(raw):
    n = money(raw)
    if n < 0:
        raise TradeBlocked("Broker reported negative quantity")
    return n


@dataclass(frozen=True)
class BrokerOrderAudit:
    client_order_id: str
    parent_order_id: Optional[str]
    status: str
    has_verified_protective_stop: bool
    paper_exposure_shares: float
    blockers: tuple[str, ...]
    broker_writes: int = 0

    def to_dict(self):
        return asdict(self)


def inspect_nested_oto(plan: PaperOrderPlan, parent, *, position_qty) -> BrokerOrderAudit:
    """Compare Alpaca's nested parent+child to durable approved intent.

    Caller MUST fetch the parent using get_order_by_id(...nested=True)
    after resolving the specific client_order_id. All broker amounts are
    untrusted. If exposure remains, stop must be ACTIVE and cover it.
    No active stop is needed for zero exposure and an unfilled entry,
    but that is not an execution authorization.
    """
    if not isinstance(plan, PaperOrderPlan):
        raise TradeBlocked("Missing durable approved paper entry plan")
    qty = quantity(position_qty)
    expected_qty = Decimal(plan.whole_shares)
    parent_id = getattr(parent, "id", None)
    pid = str(parent_id) if parent_id is not None else None
    cid = getattr(parent, "client_order_id", None)
    blockers = []
    if (cid != plan.client_order_id
            or field(parent, "symbol").upper() != plan.symbol
            or field(parent, "order_class") != "oto"
            or field(parent, "side") != "buy"
            or field(parent, "type") != "limit"
            or field(parent, "time_in_force") != "day"
            or pid is None):
        blockers.append("broker_parent_identity_or_type_mismatch")
    try:
        order_qty = quantity(getattr(parent, "qty", None))
        fill_qty = quantity(getattr(parent, "filled_qty", None))
        limit_price = money(getattr(parent, "limit_price", None))
        if (order_qty != expected_qty or fill_qty > order_qty
                or limit_price <= 0
                or abs(limit_price - money(plan.estimated_entry)) > Decimal(".001")):
            blockers.append("broker_parent_quantity_or_price_mismatch")
    except TradeBlocked:
        blockers.append("broker_parent_quantity_or_price_unavailable")
        fill_qty = Decimal("0")
    parent_status = field(parent, "status")
    if not parent_status:
        blockers.append("broker_parent_status_missing")
    if qty > expected_qty:
        blockers.append("broker_position_exceeds_authorized_shares")
    # Matching symbols alone cannot explain a position larger than the
    # number of parent shares actually filled into this account.
    if qty > fill_qty:
        blockers.append("broker_position_exceeds_parent_filled_shares")
    if parent_status in ENTRY_TERMINAL_UNFILLED and (qty or fill_qty):
        blockers.append("terminal_parent_has_unresolved_position")
    if parent_status in ENTRY_OPEN and fill_qty:
        blockers.append("partially_filled_entry_may_lack_stop")
    if parent_status not in ENTRY_OPEN | ENTRY_TERMINAL_UNFILLED | {"filled", "partially_filled"}:
        blockers.append("unknown_broker_parent_state")
    if parent_status == "partially_filled":
        blockers.append("partially_filled_entry_may_lack_stop")

    legs = getattr(parent, "legs", None)
    if legs is None:
        legs = []
    if not isinstance(legs, (list, tuple)):
        legs = []
        blockers.append("invalid_broker_legs_response")
    # A valid STOP is not protective when the same parent also has
    # another active SELL exit. Both children could attempt to liquidate
    # the shares, so require exactly one active sell exit regardless
    # of its trigger price or order type.
    active_sell_exits = [
        leg for leg in legs
        if (field(leg, "side") == "sell"
            and field(leg, "status") in PROTECTIVE_ACTIVE)
    ]
    if qty > 0 and len(active_sell_exits) != 1:
        blockers.append("unexpected_active_sell_exit_count")
    protective = []
    for leg in legs:
        try:
            if (field(leg, "symbol").upper() != plan.symbol
                    or field(leg, "side") != "sell"
                    or field(leg, "type") not in VALID_CHILD_TYPES):
                continue
            trigger = money(getattr(leg, "stop_price", None))
            if trigger <= 0 or abs(trigger - money(plan.protective_stop)) > Decimal(".001"):
                continue
            # The child must have its own stable ID. A reported
            # parent_order_id that is present must agree with the nested
            # OTO parent. Some broker nested views omit that field, so
            # actual nesting is the primary linkage evidence.
            if not getattr(leg, "id", None):
                continue
            parent_ref = getattr(leg, "parent_order_id", None)
            if parent_ref is not None and str(parent_ref) != pid:
                continue
            leg_qty = quantity(getattr(leg, "qty", None))
            leg_filled = quantity(getattr(leg, "filled_qty", None))
            if leg_filled > leg_qty or leg_qty > expected_qty:
                continue
            if leg_qty - leg_filled >= qty and leg_qty > 0:
                protective.append(leg)
        except TradeBlocked:
            continue
    active = [x for x in protective if field(x, "status") in PROTECTIVE_ACTIVE]
    filled_stop = [x for x in protective if field(x, "status") in EXIT_FINISHED]
    if qty > 0 and not active:
        blockers.append("unprotected_open_paper_position")
    if qty == 0 and fill_qty > 0 and not filled_stop:
        # Position could have been manually closed or sold by another
        # order. No assumption about P&L or protective exit allowed.
        blockers.append("filled_entry_flat_without_verified_stop_exit")
    if qty > 0 and len(active) != 1:
        blockers.append("unexpected_protective_order_count")
    if qty > 0 and len(active) == 1:
        # Alpaca does not specify a separate child TIF in the DAY OTO
        # entry request. Never *assume* the leg persists overnight.
        if field(active[0], "time_in_force") != "gtc":
            blockers.append("overnight_protective_stop_gtc_not_verified")
    if parent_status == "filled" and fill_qty != expected_qty:
        blockers.append("filled_parent_has_wrong_fill_quantity")
    return BrokerOrderAudit(
        client_order_id=plan.client_order_id,
        parent_order_id=pid,
        status=parent_status,
        has_verified_protective_stop=(not blockers and qty > 0
                                     and len(active) == 1
                                     and field(active[0], "time_in_force") == "gtc"),
        paper_exposure_shares=float(qty),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def recover_by_client_id(trading, plan: PaperOrderPlan, *, position_qty,
                         paper_endpoint=PAPER_ENDPOINT) -> BrokerOrderAudit:
    """Get the broker parent by *idempotent* client ID and GET nested legs.

    A 404 is NOT authorization to re-submit a reserved order. After an
    API timeout, absence may be transient. Treat all other API errors
    and inconsistent parent IDs as an uncertain state.
    """
    if paper_endpoint != PAPER_ENDPOINT:
        raise TradeBlocked("Broker endpoint is not Alpaca PAPER")
    if not isinstance(plan, PaperOrderPlan):
        raise TradeBlocked("Cannot reconcile order without durable plan")
    from alpaca.common.exceptions import APIError
    from alpaca.trading.requests import GetOrderByIdRequest
    try:
        indexed = trading.get_order_by_client_id(plan.client_order_id)
    except APIError as exc:
        if exc.status_code == 404:
            return BrokerOrderAudit(
                client_order_id=plan.client_order_id,
                parent_order_id=None, status="not_found_unresolved",
                has_verified_protective_stop=False,
                paper_exposure_shares=float(quantity(position_qty)),
                blockers=("broker_client_order_not_found_not_safe_to_retry",),
            )
        raise TradeBlocked("Broker order lookup unavailable") from None
    except Exception:
        raise TradeBlocked("Broker order lookup unavailable") from None
    if getattr(indexed, "client_order_id", None) != plan.client_order_id:
        raise TradeBlocked("Broker order lookup returned wrong client ID")
    pid = getattr(indexed, "id", None)
    if not pid:
        raise TradeBlocked("Broker order lookup missing parent ID")
    try:
        nested = trading.get_order_by_id(
            pid, filter=GetOrderByIdRequest(nested=True))
    except Exception:
        raise TradeBlocked("Nested broker stop-leg lookup unavailable") from None
    if str(getattr(nested, "id", None)) != str(pid):
        raise TradeBlocked("Nested broker parent ID changed unexpectedly")
    return inspect_nested_oto(plan, nested, position_qty=position_qty)
