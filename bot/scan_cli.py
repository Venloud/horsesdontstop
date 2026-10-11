"""Read-only market scan; never sends trading orders."""
import argparse
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from .scanner import scan
from .journal import record_scan

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="AAPL,MSFT,NVDA,AMD,TSLA")
    p.add_argument("--db", default=".state/research.sqlite3")
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",")]
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("Missing Alpaca paper data credentials")
    client = StockHistoricalDataClient(key, secret)
    candidates = scan(client, symbols)
    for c in candidates:
        print(f"{c.symbol:5} close={c.close:.2f} change={c.change_pct:+.2f}% rvol={c.relative_volume:.2f} score={c.score:+.2f}")
    count = record_scan(args.db, datetime.now(timezone.utc).isoformat(), candidates)
    print(f"Recorded {count} observations in {args.db}; no orders placed.")
    print("WARNING: IEX-only daily data may be incomplete versus consolidated market data.")

if __name__ == "__main__":
    main()
