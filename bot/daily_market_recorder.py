"""Record each completed market day's bars locally. Read-only market data; no orders.

Stores split-adjusted Alpaca IEX daily bars, one CSV per symbol, under
``%LOCALAPPDATA%\\horsesdontstop\\market-history``. The first run backfills
from BACKFILL_START; later runs append only new completed sessions, so the
history grows by one day each trading day and the strategy scoreboard can
replay every candidate on it.

Universe: the 15 sector-mapped stocks the bot already knows (strategies.
SECTOR_ETFS), their sector ETFs, the full SPDR sector ETF list and SPY.
A fixed, hand-picked list of surviving large caps: survivorship-biased.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

from .strategies import SECTOR_ETFS, DailyBar

BACKFILL_START = date(2021, 1, 4)
SECTOR_ETF_LIST = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
STOCKS = tuple(sorted(SECTOR_ETFS))
UNIVERSE = tuple(sorted(set(STOCKS) | set(SECTOR_ETFS.values()) | set(SECTOR_ETF_LIST) | {"SPY"}))
FIELDS = ("day", "open", "high", "low", "close", "volume")


def default_folder() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop" / "market-history"


def read_history(folder: Path, symbols: Sequence[str] = UNIVERSE) -> dict:
    out = {}
    for symbol in symbols:
        path = folder / f"{symbol}.csv"
        if not path.exists():
            out[symbol] = []
            continue
        with open(path, newline="", encoding="utf-8") as f:
            out[symbol] = [DailyBar(date.fromisoformat(r["day"]), float(r["open"]), float(r["high"]),
                                    float(r["low"]), float(r["close"]), float(r["volume"]))
                           for r in csv.DictReader(f)]
    return out


def merge_bars(existing: Sequence[DailyBar], new: Sequence[DailyBar]) -> list:
    """Append only sessions later than the last stored one (never rewrite history)."""
    last = existing[-1].day if existing else None
    added = [b for b in sorted(new, key=lambda b: b.day) if last is None or b.day > last]
    return list(existing) + added


def write_history(folder: Path, symbol: str, bars: Sequence[DailyBar]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}.csv"
    temp = path.with_suffix(".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for b in bars:
            w.writerow((b.day.isoformat(), b.open, b.high, b.low, b.close, b.volume))
    os.replace(temp, path)


def fetch(client, symbols: Sequence[str], start: date, end_exclusive: date) -> Mapping[str, list]:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), timezone.utc),
        end=datetime.combine(end_exclusive, datetime.min.time(), timezone.utc),
        feed=DataFeed.IEX, adjustment=Adjustment.SPLIT))
    out = {}
    for s in symbols:
        rows = sorted(resp.data.get(s, []), key=lambda b: b.timestamp)
        out[s] = [DailyBar(b.timestamp.date(), float(b.open), float(b.high), float(b.low),
                           float(b.close), float(b.volume)) for b in rows]
    return out


def record(client, folder: Path, today: date) -> dict:
    """Fetch completed sessions (strictly before `today`) and append them."""
    have = read_history(folder)
    summary = {}
    # Known limit: a future stock split changes split-adjusted history; delete
    # that symbol's CSV to refetch it. Appends never rewrite stored days.
    for symbol in UNIVERSE:
        start = (have[symbol][-1].day + timedelta(days=1)) if have[symbol] else BACKFILL_START
        if start >= today:
            summary[symbol] = 0
            continue
        new = fetch(client, [symbol], start, today)[symbol]
        merged = merge_bars(have[symbol], new)
        if len(merged) != len(have[symbol]):
            write_history(folder, symbol, merged)
        summary[symbol] = len(merged) - len(have[symbol])
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, default=default_folder())
    args = parser.parse_args(argv)
    key, secret = os.environ.get("ALPACA_PAPER_API_KEY"), os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        print("BLOCKED: market-data credentials missing")
        return 2
    from alpaca.data.historical import StockHistoricalDataClient
    from zoneinfo import ZoneInfo
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    # Before 16:15 NY today's session is not complete: record through yesterday.
    today = now_ny.date() if now_ny.hour * 60 + now_ny.minute < 16 * 60 + 15 else now_ny.date() + timedelta(days=1)
    summary = record(StockHistoricalDataClient(key, secret), args.folder, today)
    added = sum(summary.values())
    spy = read_history(args.folder, ["SPY"])["SPY"]
    print(f"MARKET RECORDER: +{added} bars across {len(UNIVERSE)} symbols; "
          f"SPY history {len(spy)} sessions through {spy[-1].day if spy else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
