"""Read-only lifecycle verification for the supervised PAPER pilot.

Uses only the monitor login (SELECT) and the GET-only broker client. Prints one
JSON object with the lifecycle stage of today's entry:
  NO_ENTRY | AUTHORIZED | SUBMITTED | ACCEPTED | FILLED_PROTECTED |
  EXITED | CANCELED | PROBLEM
"""
import json
import os
import sys
from datetime import datetime, timezone


def main():
    import psycopg
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrderByIdRequest, GetOrdersRequest
    from .paper_broker_transport import make_reconciler_paper_client
    from .paper_reconcile import reconcile_observation
    from .paper_store import PostgresPaperJournal

    out = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "problems": []}
    monitor = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
    with psycopg.connect(os.environ["PAPER_JOURNAL_DSN"], sslmode="require", connect_timeout=10) as c:
        out["controls"] = list(c.execute(
            "SELECT kill_switch_active, submissions_enabled FROM paper_control").fetchone())
        out["policy_enabled"] = c.execute(
            "SELECT autonomous_entries_enabled FROM paper_trading_policy").fetchone()[0]
        row = c.execute(
            "SELECT client_order_id, status, symbol, whole_shares, expected_entry, "
            "protective_stop, broker_order_id FROM paper_intents WHERE status <> 'expired' "
            "AND trading_day = (clock_timestamp() AT TIME ZONE 'America/New_York')::date "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            cid = row[0]
            out["intent"] = dict(zip(("client_order_id", "status", "symbol", "shares",
                                      "limit", "stop", "broker_order_id"),
                                     [str(x) if x is not None else None for x in row]))
            out["audit"] = [list(map(str, a)) for a in c.execute(
                "SELECT audit_id, kind, summary, created_at FROM paper_audit_events "
                "WHERE client_order_id = %s ORDER BY audit_id", (cid,)).fetchall()]
    if "intent" not in out:
        out["stage"] = "NO_ENTRY"
        print(json.dumps(out, indent=2))
        return 0

    broker = make_reconciler_paper_client(os.environ["ALPACA_PAPER_API_KEY"],
                                          os.environ["ALPACA_PAPER_SECRET_KEY"])
    from alpaca.common.exceptions import APIError
    intent = out["intent"]
    parent = None
    try:
        found = broker.get_order_by_client_id(intent["client_order_id"])
        parent = broker.get_order_by_id(found.id, filter=GetOrderByIdRequest(nested=True))
    except APIError as exc:
        if exc.status_code != 404:
            out["problems"].append("broker_lookup_failed")
    val = lambda o, k: str(getattr(getattr(o, k, None), "value", getattr(o, k, None)) or "").lower()
    if parent is not None:
        out["broker_parent"] = {k: val(parent, k) for k in (
            "id", "status", "side", "type", "order_class", "time_in_force", "qty",
            "filled_qty", "limit_price", "filled_avg_price")}
        out["broker_legs"] = [{k: val(l, k) for k in (
            "id", "status", "side", "type", "time_in_force", "qty", "filled_qty", "stop_price")}
            for l in (getattr(parent, "legs", None) or [])]
    positions = broker.get_all_positions()
    orders = broker.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500))
    out["positions"] = [{"symbol": p.symbol, "qty": str(p.qty),
                         "unrealized_pl": str(getattr(p, "unrealized_pl", ""))} for p in positions]
    rec = reconcile_observation(positions, orders, monitor.read_unresolved_intents(limit=501),
                                order_fetch_complete=len(orders) < 500,
                                approved_stops=monitor.read_approved_stop_intents(limit=501))
    out["reconciliation"] = rec.to_dict()
    kinds = {a[1] for a in out.get("audit", [])}
    out["policy_authorized_audited"] = "policy_authorized" in kinds

    status = intent["status"]
    pstatus = out.get("broker_parent", {}).get("status")
    if not out["policy_authorized_audited"]:
        out["problems"].append("missing_policy_authorized_audit")
    if status == "filled" and positions and rec.managed_exposure:
        out["stage"] = "FILLED_PROTECTED"
    elif status == "filled" and not positions:
        out["stage"] = "EXITED"
    elif status in ("canceled", "rejected"):
        out["stage"] = "CANCELED"
    elif status in ("accepted", "submitted") or pstatus in ("new", "accepted", "pending_new"):
        out["stage"] = "ACCEPTED"
    elif status == "unknown" and parent is not None:
        out["stage"] = "SUBMITTED"
    elif status == "reserved":
        out["stage"] = "AUTHORIZED"
    else:
        out["stage"] = "PROBLEM"
    if positions and not rec.managed_exposure:
        out["problems"].append("position_not_verified_protected")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
