"""Historical strategy comparison with a chronological 70/30 holdout.

Research only. Does NOT instantiate Alpaca TradingClient or send orders.
Each strategy/risk combination gets its own independent $500 *simulation*,
while symbols inside a simulation compete for ONE shared cash balance.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

from .portfolio_research import run_portfolio
from .strategies import DailyBar, SECTOR_ETFS, STRATEGIES


DEFAULT_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMD", "TSLA")


def load_bars(client, symbols, days):
    # UTC start-of-day excludes the current UTC calendar day, preventing a
    # partially completed US session from being treated as a full session.
    end = datetime.now(timezone.utc).replace(hour=0, minute=0,
                                              second=0, microsecond=0)
    start = end - timedelta(days=days)
    required = sorted(set(symbols) | {"SPY"} |
                      {SECTOR_ETFS[s] for s in symbols if s in SECTOR_ETFS})
    response = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=required, timeframe=TimeFrame.Day,
        start=start, end=end, feed=DataFeed.IEX,
        adjustment=Adjustment.SPLIT))
    data = {}
    for symbol in required:
        rows = sorted(response.data.get(symbol, []), key=lambda b: b.timestamp)
        data[symbol] = [
            DailyBar(b.timestamp.date(), float(b.open), float(b.high),
                     float(b.low), float(b.close), float(b.volume))
            for b in rows
        ]
    return start, end, data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--output", default=".state/strategy-comparison.json")
    args = parser.parse_args()
    symbols = tuple(s.strip().upper() for s in args.symbols.split(","))
    if (not 1 <= len(symbols) <= 10 or len(symbols) != len(set(symbols))
            or any(not s.isalpha() or len(s) > 5 for s in symbols)
            or "SPY" in symbols):
        parser.error("Use 1-10 unique simple US stock ticker symbols, excluding SPY")
    if not 500 <= args.days <= 1095:
        parser.error("--days must be 500-1095 (52-week warmup required)")
    load_dotenv()
    key, secret = (os.getenv("ALPACA_PAPER_API_KEY"),
                   os.getenv("ALPACA_PAPER_SECRET_KEY"))
    if not key or not secret:
        parser.error("Paper market-data credentials required")
    client = StockHistoricalDataClient(key, secret)
    start, end, data = load_bars(client, symbols, args.days)
    n_days = len(data["SPY"])
    if n_days < 330:
        raise SystemExit("Insufficient SPY sessions for a 52-week training window")
    cutoff = int(n_days * .70)
    if cutoff <= 253 or n_days - cutoff < 50:
        raise SystemExit("Insufficient train or holdout sessions")
    reports = []
    for strategy in STRATEGIES:
        for risk in (.01, .02):
            for label, lo, hi in (("train", 0, cutoff),
                                  ("holdout", cutoff, n_days)):
                report = run_portfolio(data, strategy, symbols, risk_fraction=risk,
                                       start_index=lo, end_index=hi)
                report["period"] = label
                reports.append(report)
                summary = {k: v for k, v in report.items() if k != "trades"}
                print(json.dumps(summary, sort_keys=True))
    out = {
        "model": "Fixed hypotheses, no parameter fitting, no broker orders",
        "market_data": "Alpaca IEX daily bars, split-adjusted; not consolidated SIP",
        "from_utc": start.isoformat(),
        "to_utc_exclusive": end.isoformat(),
        "watchlist": list(symbols),
        "train_fraction": .70,
        "holdout_fraction": .30,
        "starting_cash_per_independent_strategy_test": 500,
        "known_limitations": [
            "A hand-picked stock universe creates selection and survivorship bias.",
            "Only one whole-share long position per simulated portfolio.",
            "No fractional shares, shorting, dividends, tax or real-world stop-order guarantees.",
            "10 bps entry and exit adverse slippage assumed; no brokerage/regulatory fees.",
            "Daily OHLC does not reveal intraday fill sequencing or order-book liquidity.",
            "Max drawdown is daily-close marked, not intraday worst case.",
            "Market data and sector mapping may be incomplete or stale.",
            "This is a chronological split, not a full walk-forward optimization study.",
            "Signals are constructed using previous completed daily bars.",
            "Sector research covers only explicitly mapped sample stocks.",
            "Earnings drift NOT implemented; requires point-in-time earnings surprise data."
        ],
        "reports": reports,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(out, file, indent=2)
    print(f"Saved {len(reports)} research comparisons to {args.output}. "
          "No Alpaca orders submitted.")


if __name__ == "__main__":
    main()
