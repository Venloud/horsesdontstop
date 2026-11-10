"""Operator-requested close of bot PAPER positions (same guarded exit routine).

  --dry-run : show what would be closed
  --symbol S: close only S (default: every bot position)
Only positions explained by a filled journal intent with one live GTC stop leg
are closed; anything else is reported and left untouched.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbol")
    args = parser.parse_args(argv)
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .paper_broker_transport import make_supervised_paper_client
    from .paper_exit_manager import execute_exit, plan_exits
    from .paper_reconcile import orders_with_recent_parents
    from .paper_safety import PAPER_ENDPOINT
    from .paper_store import PostgresPaperJournal
    from .paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED
    from .paper_executor_service import _account_hash, _notify, _record_exit

    trading = make_supervised_paper_client(os.environ["ALPACA_PAPER_API_KEY"],
                                           os.environ["ALPACA_PAPER_SECRET_KEY"])
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        print("BLOCKED: client is not Alpaca PAPER"); return 2
    if _account_hash(trading.get_account()) != os.environ["PAPER_ACCOUNT_SHA256"]:
        print("BLOCKED: account does not match pinned PAPER identity"); return 2
    monitor = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
    monitor.assert_monitor_only()
    now = datetime.now(timezone.utc)
    positions = trading.get_all_positions()
    if args.symbol:
        positions = [p for p in positions if p.symbol == args.symbol.upper()]
    open_orders = trading.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, nested=True, limit=500))
    orders, _ = orders_with_recent_parents(trading, open_orders, now=now)
    actions = plan_exits(positions=positions, orders=orders,
                         approved_stops=monitor.read_approved_stop_intents(limit=501),
                         now=now, market={}, operator_close=True)
    covered = {a.symbol for a in actions}
    for p in positions:
        if p.symbol not in covered:
            print(f"NOT TOUCHED: {p.symbol} {p.qty} (not a journaled bot position with a live stop)")
    if not actions:
        print("Nothing to close."); return 0
    released = PAPER_ORDER_SUBMISSIONS_RELEASED is True and not args.dry_run
    code = 0
    for action in actions:
        print(f"{'DRY RUN' if not released else 'CLOSING'}: {action.shares} {action.symbol} "
              f"(entry {action.client_order_id}, stop leg {action.stop_leg_id})")
        try:
            result = execute_exit(trading, action, released=released)
        except Exception as exc:
            print(f"BLOCKED: {exc}"); code = 2
            _notify("close blocked", f"{action.symbol}: {type(exc).__name__}", "urgent")
            continue
        print(json.dumps(result, default=str))
        if released:
            _record_exit(result)
            _notify("position closed", f"operator_close: SELL {result.get('shares')} "
                    f"{action.symbol} fill {result.get('fill_price')}", "high")
    return code


if __name__ == "__main__":
    sys.exit(main())
