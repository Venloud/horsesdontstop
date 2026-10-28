"""Sanitized Windows-host evidence. Broker GET and Neon SELECT only.

Credentials are injected by the existing user's DPAPI mechanism, never
printed. This program cannot reserve intents, alter grants or submit orders.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.paper_safety import PAPER_ENDPOINT


def probe():
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from bot.paper_readiness_cli import research_candidates
    from bot.robustness_cli import BROADER_SYMBOLS
    from bot.paper_store import PostgresPaperJournal

    report = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "target_session": "2026-10-29", "broker_writes": 0,
              "database_writes": 0, "decision": "BLOCKED",
              "order_proposed": None}
    key, secret = os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
    trading = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    try:
        account, clock = trading.get_account(), trading.get_clock()
        positions = trading.get_all_positions()
        orders = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, nested=True, limit=500))
        sessions = trading.get_calendar(GetCalendarRequest(
            start=date(2026, 10, 29), end=date(2026, 10, 29)))
        report["broker"] = {
            "authenticated_paper_reads": True, "market_open": clock.is_open,
            "equity": str(account.equity), "cash": str(account.cash),
            "buying_power": str(account.buying_power),
            "trading_blocked": account.trading_blocked,
            "positions": len(positions), "open_orders": len(orders),
            "target_calendar": [{"day": str(s.date), "open_ny": str(s.open),
                                 "close_ny": str(s.close)} for s in sessions],
        }
        report["no_trade_reasons"] = ["security_migration_and_runtime_release_not_approved"]
        if clock.is_open is not True:
            report["no_trade_reasons"].append("market_closed_no_executable_fresh_quote")
    except Exception:
        report["broker"] = {"authenticated_paper_reads": False}
        report["no_trade_reasons"] = ["broker_reads_unavailable"]
    try:
        journal = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
        journal.assert_monitor_only()
        with journal._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT current_user, kill_switch_active, submissions_enabled, "
                            "(SELECT count(*) FROM public.paper_intents), "
                            "(SELECT max(observed_at) FROM public.paper_equity_snapshots) "
                            "FROM public.paper_control WHERE singleton=TRUE")
                row = cur.fetchone()
        report["neon"] = {"monitor_role": row[0], "kill": row[1], "enabled": row[2],
                          "intents": row[3], "latest_opening_equity_utc": str(row[4])}
    except Exception:
        report["neon"] = {"monitor_reads_verified": False}
    try:
        data = StockHistoricalDataClient(key, secret)
        candidates = research_candidates(data, BROADER_SYMBOLS)
        report["completed_bar_research_only"] = candidates[:3]
        report["research_is_not_order_authorization"] = True
    except Exception:
        report["market_data"] = "unavailable_no_signal_inferred"
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), sort_keys=True))
    except Exception:
        print(json.dumps({"decision": "BLOCKED", "reason": "credential_or_probe_setup_unavailable",
                          "broker_writes": 0, "database_writes": 0}))
        raise SystemExit(2)
