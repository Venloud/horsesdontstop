"""Read-only Alpaca IEX daily-bar backtest. No broker orders."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

from .simulator import Bar, simulate


def summarize(symbol, risk, result, first_close, last_close):
    trades = result["trades"]
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    gross_loss = -sum(losses)
    return {
        "symbol": symbol, "risk_pct": risk * 100,
        "initial_equity": result["initial_cash"],
        "final_equity": result["final_equity"],
        "net_pnl": result["net_pnl"],
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2) if trades else None,
        "profit_factor": round(sum(wins) / gross_loss, 3) if gross_loss else None,
        "buy_hold_return_pct": round(100 * (last_close / first_close - 1), 2),
        "note": "Exploratory daily-bar single-symbol backtest; not a validated strategy."
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="AAPL,MSFT,NVDA,AMD,TSLA")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output", default=".state/backtest-report.json")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    if not 1 <= len(symbols) <= 10 or any(not s.isalpha() or len(s) > 5 for s in symbols):
        parser.error("Use 1-10 simple uppercase stock symbols")
    if not 60 <= args.days <= 730:
        parser.error("--days must be 60-730")
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        parser.error("Paper market-data credentials required")
    client = StockHistoricalDataClient(key, secret)
    # End yesterday UTC to avoid using an incomplete current-session daily bar.
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    response = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
        start=start, end=end, feed=DataFeed.IEX))
    reports = []
    for symbol in symbols:
        raw = sorted(response.data.get(symbol, []), key=lambda b: b.timestamp)
        bars = [Bar(float(b.open), float(b.high), float(b.low), float(b.close)) for b in raw]
        if len(bars) < 20:
            print(f"{symbol}: insufficient historical bars ({len(bars)})")
            continue
        for risk in (0.01, 0.02):
            report = summarize(symbol, risk, simulate(bars, risk_fraction=risk),
                               bars[0].close, bars[-1].close)
            reports.append(report)
            print(json.dumps(report, sort_keys=True))
    if not reports:
        raise SystemExit("No usable history; no report produced")
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump({"source": "Alpaca IEX daily bars", "from_utc": start.isoformat(),
                   "to_utc_exclusive": end.isoformat(), "reports": reports},
                  handle, indent=2)
    print(f"Saved {len(reports)} exploratory results to {args.output}; no orders placed.")


if __name__ == "__main__":
    main()
