"""Current broker GET confirmation of nested PAPER filled-entry protective stops.

A list-orders response is not sufficient. For EACH observed position,
match exactly one durable approved stop and use the exact Alpaca client
order ID to refetch the parent and its nested stop child. No writes.
This is NOT a promise that a stop market fill cannot gap/slip.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .paper_reconcile import reconcile_observation, value
from .paper_safety import PAPER_ENDPOINT, money


def confirm_nested_filled_stops(trading, positions, approvals):
    """Return symbols lacking verified fresh broker-side stop evidence.

    A missing/malformed order, wrong client ID, mismatched parent ID,
    uncertain status, partial fill or GTC mismatch never passes. Ignore
    untrusted exception text, which can contain credentials.
    """
    uncovered = []
    for position in positions:
        symbol = getattr(position, "symbol", None)
        if not isinstance(symbol, str) or not symbol:
            uncovered.append("unknown_position")
            continue
        try:
            matching = [a for a in approvals
                        if isinstance(a, (tuple, list)) and len(a) in (6, 7)
                        and a[2] == symbol and a[1] in ("accepted", "filled")]
            if len(matching) != 1:
                raise ValueError("No unique durable approved stop")
            approved = matching[0]
            client_id, _status, _symbol, qty, stop, parent_id = approved
            approved_tif = approved[6] if len(approved) == 7 else "day"
            if (not isinstance(client_id, str) or not client_id
                    or not parent_id or money(qty) <= 0 or money(stop) <= 0):
                raise ValueError("Invalid stop terms")
            indexed = trading.get_order_by_client_id(client_id)
            if (getattr(indexed, "client_order_id", None) != client_id
                    or str(getattr(indexed, "id", None)) != str(parent_id)):
                raise ValueError("Wrong broker parent")
            from alpaca.trading.requests import GetOrderByIdRequest
            nested = trading.get_order_by_id(
                getattr(indexed, "id"), filter=GetOrderByIdRequest(nested=True))
            if (str(getattr(nested, "id", None)) != str(parent_id)
                    or getattr(nested, "client_order_id", None) != client_id
                    or value(getattr(nested, "time_in_force", None)) != approved_tif
                    or value(getattr(nested, "order_class", None)) != "oto"
                    or value(getattr(nested, "side", None)) != "buy"
                    or value(getattr(nested, "type", None)) != "limit"
                    or value(getattr(nested, "status", None)) != "filled"):
                raise ValueError("Nested entry mismatch")
            # Refuse a second sell exit even when it is a different
            # order TYPE, or pending cancellation. Such a sibling may
            # execute after the protected child and oversell shares.
            legs = getattr(nested, "legs", None)
            if not isinstance(legs, (list, tuple)):
                raise ValueError("No valid nested child-order list")
            possibly_live_sells = [
                leg for leg in legs
                if value(getattr(leg, "side", None)) == "sell"
                and value(getattr(leg, "status", None))
                    not in ("canceled", "rejected", "expired", "filled")
            ]
            if len(possibly_live_sells) != 1:
                raise ValueError("Competing or missing sell child")
            stop_leg = possibly_live_sells[0]
            if (value(getattr(stop_leg, "type", None)) != "stop"
                    or value(getattr(stop_leg, "time_in_force", None)) != "gtc"):
                raise ValueError("Nested stop missing or not GTC")
            expiry = getattr(stop_leg, "expires_at", None)
            if expiry is not None:
                if isinstance(expiry, str):
                    expiry = datetime.fromisoformat(
                        expiry.replace("Z", "+00:00"))
                if (not isinstance(expiry, datetime)
                        or expiry.tzinfo is None
                        or expiry.astimezone(timezone.utc)
                        <= datetime.now(timezone.utc)):
                    raise ValueError("GTC stop expiration unverified")
            audit = reconcile_observation(
                [position], [nested], [], order_fetch_complete=True,
                approved_stops=[approved])
            # Positions legitimately keep overall reconciliation critical;
            # ONLY the stop-specific proof is extracted here.
            if audit.uncovered_symbols or "position_missing_verified_gtc_oto_stop" in audit.blockers:
                raise ValueError("No current GTC stop protection")
        except Exception:
            uncovered.append(symbol)
    return tuple(sorted(set(uncovered)))
