"""Price history for stocks that insiders bought (copy-trading backtests). Data only.

Fetches Alpaca IEX daily bars once per symbol into
%LOCALAPPDATA%\\horsesdontstop\\market-history-extra, drops illiquid names
(median close < $5 or median IEX dollar volume < $50k, roughly $2M real
volume), and keeps only the sessions around each purchase (10 before to
140 after) so thousands of symbols fit in memory. Symbols Alpaca no longer
lists (delisted) are missing: survivorship bias flatters the backtest.
"""
from __future__ import annotations

import json
import os
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .daily_market_recorder import BACKFILL_START, _bar, read_history, write_history

RUNTIME = Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop"
EXTRA = RUNTIME / "market-history-extra"
META = RUNTIME / "insider-bars-meta.json"
MIN_SIGNAL_VALUE = 100_000.0
BEFORE, AFTER = 10, 140


def signal_symbols(events, min_value=MIN_SIGNAL_VALUE) -> dict[str, list[date]]:
    tot = defaultdict(float)
    for e in events:
        tot[(e["symbol"], e["filed"])] += e["value"]
    out = defaultdict(list)
    for (sym, day), v in tot.items():
        if v >= min_value and sym.isalpha() and len(sym) <= 5:
            out[sym].append(day)
    return {k: sorted(v) for k, v in out.items()}


def fetch_missing(symbols, *, today: date | None = None, batch: int = 100, log=print) -> int:
    key, secret = os.environ.get("ALPACA_PAPER_API_KEY"), os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        return 0
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(key, secret)
    meta = json.loads(META.read_text()) if META.exists() else {}
    today = today or date.today()
    todo = [s for s in symbols if meta.get(s, "") < (today - timedelta(days=1)).isoformat()]
    fetched = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        have = read_history(EXTRA, chunk)
        start = min(((have[s][-1].day + timedelta(days=1)) if have[s] else BACKFILL_START) for s in chunk)
        try:
            resp = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                start=datetime.combine(start, datetime.min.time(), timezone.utc),
                end=datetime.combine(today, datetime.min.time(), timezone.utc),
                feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)).data
        except Exception as exc:
            log(f"  insider bars batch failed: {type(exc).__name__}")
            continue
        for s in chunk:
            rows = [x for x in (_bar(b) for b in sorted(resp.get(s, []), key=lambda b: b.timestamp))
                    if x is not None]
            last = have[s][-1].day if have[s] else None
            new = [b for b in rows if last is None or b.day > last]
            if new:
                write_history(EXTRA, s, list(have[s]) + new)
            meta[s] = today.isoformat()
            fetched += 1
        META.parent.mkdir(parents=True, exist_ok=True)
        META.write_text(json.dumps(meta))
        log(f"  insider bars {min(i + batch, len(todo))}/{len(todo)}")
    return fetched


def liquid(bars) -> bool:
    if len(bars) < 20:
        return False
    return (median(b.close for b in bars) >= 5.0
            and median(b.close * b.volume for b in bars) >= 50_000.0)


def trimmed(bars, signal_days) -> list:
    days = [b.day for b in bars]
    keep = set()
    for d in signal_days:
        i = bisect_left(days, d)
        keep.update(range(max(0, i - BEFORE), min(len(bars), i + AFTER)))
    return [bars[i] for i in sorted(keep)]


def extend_with_insider_bars(data: dict, events, *, fetch: bool = True, log=print) -> dict:
    sig = signal_symbols(events)
    extra_syms = [s for s in sig if s not in data]
    if fetch:
        fetch_missing(extra_syms, log=log)
    out = dict(data)
    hist = read_history(EXTRA, extra_syms)
    kept = 0
    for s in extra_syms:
        bars = hist.get(s) or []
        if liquid(bars):
            out[s] = trimmed(bars, sig[s])
            kept += 1
    log(f"insider universe: {kept} liquid symbols of {len(extra_syms)} bought by insiders")
    return out
