"""Real-time IEX stock quotes + Alpaca PAPER account, zero orders.

Uses completed daily IEX bars for momentum selection, then insists on
a FRESH IEX level-1 quote (not consolidated SIP/NBBO), and displays
illustrative WHOLE-share 1%-risk LIMIT/OTO requests. Submission is
impossible from this program.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

from .live_quotes import (
    build_live_protected_oto_request, preview_live_paper_entry,
)
from .paper_readiness_cli import research_candidates
from .paper_safety import PAPER_ENDPOINT, TradeBlocked
from .paper_store import PostgresPaperJournal
from .robustness_cli import BROADER_SYMBOLS


def preview_live_quotes(trading, market_data, journal, *,
                        symbols=BROADER_SYMBOLS, now=None):
    now = now or datetime.now(timezone.utc)
    # Ensure a leaked administrator DSN can never be used.
    journal.assert_monitor_only()
    control = journal.read_control()
    account = trading.get_account()
    clock = trading.get_clock()
    positions = trading.get_all_positions()
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    orders = trading.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500,
                                nested=True))
    unresolved = journal.read_unresolved_intents(limit=501)
    can_research = (
        control.kill_switch_active is True
        and control.submissions_enabled is False
        and not getattr(account, "trading_blocked", True)
        and not positions and not orders and not unresolved
        and bool(clock.is_open))
    blockers = []
    if not control.kill_switch_active or control.submissions_enabled:
        blockers.append("production_paper_kill_switch_must_remain_engaged")
    if positions or orders or unresolved:
        blockers.append("account_has_existing_exposure_or_unresolved_orders")
    if getattr(account, "trading_blocked", True):
        blockers.append("paper_account_blocked")
    if not clock.is_open:
        blockers.append("stock_market_closed")
    suggestions = []
    if can_research:
        # Daily-bar signal is research, not a real-time momentum signal.
        for candidate in research_candidates(market_data, symbols, now=now)[:10]:
            symbol = candidate["symbol"]
            try:
                latest = market_data.get_stock_latest_quote(
                    StockLatestQuoteRequest(symbol_or_symbols=symbol,
                                            feed=DataFeed.IEX))
                quote = latest.get(symbol)
                if quote is None:
                    raise TradeBlocked("Missing IEX quote")
                proposal = preview_live_paper_entry(
                    symbol=symbol, quote=quote,
                    equity=account.equity, available_cash=account.cash,
                    now=now, market_open=bool(clock.is_open))
                request = build_live_protected_oto_request(proposal)
                suggestions.append({
                    "symbol": symbol, "momentum_score": candidate["momentum_score"],
                    "quote_age_seconds": proposal.quote_age_seconds,
                    "preview": proposal.to_dict(),
                    "order_type": str(request.order_class),
                    "submission_enabled": False,
                })
            except TradeBlocked as exc:
                suggestions.append({
                    "symbol": symbol,
                    "momentum_score": candidate["momentum_score"],
                    "blocked_reason": str(exc),
                    "submission_enabled": False,
                })
    return {
        "mode": "LIVE_IEX_PAPER_RESEARCH_NO_ORDERS",
        "generated_utc": now.isoformat(),
        "data_feed": "IEX_ONLY_NOT_CONSOLIDATED_NBBO",
        "paper_account_equity": round(float(account.equity), 2),
        "paper_cash": round(float(account.cash), 2),
        "market_open": bool(clock.is_open),
        "kill_switch_active": control.kill_switch_active,
        "submissions_enabled": False,
        "broker_writes": 0,
        "safety_blockers": blockers,
        "candidates": suggestions,
        "note": (
            "PREVIEW ONLY. No orders are sent. Each capped limit buy is paired "
            "with a broker-side stop request. Stop prices do not guarantee "
            "execution. IEX quotes do not equal SIP/NBBO."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".state/live-iex-paper-preview.json")
    args = parser.parse_args()
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    dsn = os.getenv("PAPER_JOURNAL_DSN")
    if not key or not secret or not dsn:
        parser.error("Paper credentials and restricted Neon journal secret required")
    trading = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    data = StockHistoricalDataClient(key, secret)
    journal = PostgresPaperJournal(dsn)
    report = preview_live_quotes(trading, data, journal)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"IEX LIVE PAPER RESEARCH: market open {report['market_open']}, "
          f"paper equity ${report['paper_account_equity']:.2f}, "
          f"candidates {len(report['candidates'])}, "
          f"blocked checks {len(report['safety_blockers'])}. "
          "ZERO orders.")
    for row in report["candidates"]:
        if row.get("preview"):
            p = row["preview"]
            print(f"PREVIEW {p['symbol']}: {p['shares']} shares, "
                  f"bid ${p['bid_usd']:.2f}, ask ${p['ask_usd']:.2f}, "
                  f"limit ${p['buy_limit_usd']:.2f}, stop ${p['stop_usd']:.2f}, "
                  "NOT SUBMITTED")
        else:
            print(f"BLOCKED {row['symbol']}: {row['blocked_reason']}")
    print("Live-stock order submission remains disabled.")


if __name__ == "__main__":
    main()
