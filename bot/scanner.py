"""Read-only, deterministic momentum ranking for a small candidate universe.

Use regular-session daily bars only. This is a research ranking, not a buy signal.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed


@dataclass(frozen=True)
class Candidate:
    symbol: str
    close: float
    change_pct: float
    relative_volume: float
    score: float


def rank_bars(symbol: str, bars: Iterable) -> Candidate | None:
    rows = sorted(list(bars), key=lambda b: b.timestamp)
    if len(rows) < 12:
        return None
    previous, latest = rows[-2], rows[-1]
    if previous.close <= 0 or latest.close <= 0:
        return None
    # Compare latest session volume against 10 earlier sessions, excluding latest.
    baseline = sum(b.volume for b in rows[-11:-1]) / 10
    if baseline <= 0:
        return None
    change_pct = (latest.close / previous.close - 1) * 100
    relative_volume = latest.volume / baseline
    # Simple transparent heuristic, not a trained AI model.
    score = change_pct * min(relative_volume, 5)
    return Candidate(symbol, float(latest.close), round(change_pct, 2),
                     round(relative_volume, 2), round(score, 2))


def scan(client: StockHistoricalDataClient, symbols: list[str], lookback_days: int = 35) -> list[Candidate]:
    if not symbols or len(symbols) > 30:
        raise ValueError("Provide 1-30 symbols")
    if any(not s.isalpha() or not 1 <= len(s) <= 5 or s != s.upper() for s in symbols):
        raise ValueError("Use uppercase US ticker symbols")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    request = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                               start=start, end=end, feed=DataFeed.IEX)
    bars = client.get_stock_bars(request)
    result = [rank_bars(s, bars.data.get(s, [])) for s in symbols]
    return sorted((x for x in result if x is not None), key=lambda x: x.score, reverse=True)
