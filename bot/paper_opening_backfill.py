"""Broker-attested opening-equity backfill (fallback when the collector missed).

Uses Alpaca's own minute portfolio history for today's 09:30 New York bar,
read through the GET-only reconciler client. Writes ONE snapshot at that bar's
timestamp only if the 09:30-09:35 window is empty. Refuses when:
  * today is not an Alpaca trading session or it is before 09:35 NY;
  * the 09:30 bar is missing, non-positive or not exactly 09:30;
  * there were no fills today but the bar differs from Alpaca's previous-close
    equity (last_equity) by more than one cent.
Neon's policy uses the MIN equity in the window, so an extra row can only
shrink limits. --dry-run prints the decision without writing.
"""
import argparse
import os
import sys
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def choose_opening_equity(timestamps, equities, *, session_day, last_equity, fills_today):
    """Pure decision: return (observed_at_utc, equity) or raise ValueError."""
    open_ny = datetime.combine(session_day, time(9, 30), NY)
    bars = {datetime.fromtimestamp(int(t), timezone.utc): e for t, e in zip(timestamps, equities)}
    bar = bars.get(open_ny.astimezone(timezone.utc))
    if bar is None:
        raise ValueError("Alpaca 09:30 portfolio-history bar missing")
    equity = Decimal(str(bar)).quantize(Decimal("0.000001"))
    if equity <= 0:
        raise ValueError("Alpaca 09:30 equity not positive")
    if fills_today == 0 and abs(equity - Decimal(str(last_equity))) > Decimal("0.01"):
        raise ValueError("No fills today but 09:30 equity differs from previous close")
    return open_ny.astimezone(timezone.utc), equity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    import psycopg
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import (GetCalendarRequest, GetOrdersRequest,
                                         GetPortfolioHistoryRequest)
    from .paper_broker_transport import make_reconciler_paper_client
    from .paper_execution_journal import PaperExecutorJournal

    now = datetime.now(timezone.utc)
    local = now.astimezone(NY)
    broker = make_reconciler_paper_client(os.environ["ALPACA_PAPER_API_KEY"],
                                          os.environ["ALPACA_PAPER_SECRET_KEY"])
    today = local.date()
    sessions = broker.get_calendar(GetCalendarRequest(start=today, end=today))
    if not sessions or getattr(sessions[0], "date", None) != today:
        print("REFUSED: not an Alpaca trading session today"); return 2
    if local.time() < time(9, 35):
        print("REFUSED: collector window still open (before 09:35 NY)"); return 2
    account = broker.get_account()
    history = broker.get_portfolio_history(GetPortfolioHistoryRequest(
        period="1D", timeframe="1Min", intraday_reporting="market_hours"))
    after = datetime.combine(today, time(9, 30), NY).astimezone(timezone.utc)
    orders = broker.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.ALL, after=after, limit=500, nested=True))
    fills = sum(1 for o in orders if Decimal(str(getattr(o, "filled_qty", 0) or 0)) > 0)
    try:
        observed, equity = choose_opening_equity(
            history.timestamp, history.equity, session_day=today,
            last_equity=account.last_equity, fills_today=fills)
    except ValueError as exc:
        print(f"REFUSED: {exc}"); return 2
    print(f"Alpaca 09:30 bar: equity={equity} at {observed.isoformat()} "
          f"(previous close {account.last_equity}, fills today {fills})")

    journal = PaperExecutorJournal(os.environ["PAPER_EXECUTOR_DSN"])
    journal.assert_executor_permissions()
    with psycopg.connect(os.environ["PAPER_EXECUTOR_DSN"], sslmode="require", connect_timeout=10) as c:
        existing = c.execute(
            "SELECT count(*) FROM public.paper_equity_snapshots WHERE observed_at >= %s "
            "AND observed_at < %s", (observed, observed + timedelta(minutes=5))).fetchone()[0]
        if existing:
            print("NO CHANGE: opening window already has a snapshot"); return 0
        if args.dry_run:
            print("DRY RUN: would insert the snapshot above"); return 0
        c.execute("INSERT INTO public.paper_equity_snapshots (observed_at, equity, cash) "
                  "VALUES (%s, %s, %s)", (observed, equity, Decimal(str(account.cash))))
    print("INSERTED broker-attested opening snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
