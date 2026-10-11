"""Deterministic, completed-bar-only research signals.

Every caller MUST pass bars ending at the prior *completed* session. These are
transparent hypotheses, not trained models or validated trading signals.
"""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Sequence


STRATEGIES = ("momentum_volume", "trend_following", "high_52_week", "sector_leadership")

# Explicit limited example universe; does not dynamically discover sector members.
SECTOR_ETFS = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK",
    "INTC": "XLK", "TSLA": "XLY", "F": "XLY", "DIS": "XLC",
    "JPM": "XLF", "BAC": "XLF", "XOM": "XLE", "CVX": "XLE",
    "KO": "XLP", "WMT": "XLP", "PFE": "XLV",
}


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        nums = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isfinite(float(x)) for x in nums):
            raise ValueError("nonfinite OHLCV")
        if (min(self.open, self.high, self.low, self.close) <= 0
                or self.volume < 0 or self.low > min(self.open, self.close)
                or self.high < max(self.open, self.close)
                or self.high < self.low):
            raise ValueError("invalid OHLCV")


def signal_score(strategy: str, history: Sequence[DailyBar],
                 sector: Sequence[DailyBar] = (),
                 benchmark: Sequence[DailyBar] = ()) -> float | None:
    """Positive score means eligible for research; None means no candidate.

    'history', 'sector' and 'benchmark' terminate before the session to trade.
    Return magnitude is meaningful only within the same strategy.
    """
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")

    if strategy == "momentum_volume":
        if len(history) < 22:
            return None
        baseline = sum(b.volume for b in history[-21:-1]) / 20
        if baseline <= 0:
            return None
        relative_volume = history[-1].volume / baseline
        momentum = history[-1].close / history[-22].close - 1
        if momentum <= 0 or relative_volume < 1.15:
            return None
        return momentum * min(relative_volume, 3.0)

    if strategy == "trend_following":
        if len(history) < 51:
            return None
        recent_ma = sum(b.close for b in history[-20:]) / 20
        old_ma = sum(b.close for b in history[-50:]) / 50
        prior_ma = sum(b.close for b in history[-21:-1]) / 20
        close = history[-1].close
        if close <= recent_ma or recent_ma <= old_ma or recent_ma <= prior_ma:
            return None
        return (close / old_ma - 1)

    if strategy == "high_52_week":
        # Require a full 252-session preceding lookback to avoid calling a
        # short-window breakout a 52-week high.
        if len(history) < 253:
            return None
        previous_high = max(b.high for b in history[-253:-1])
        close = history[-1].close
        long_momentum = close / history[-64].close - 1
        if close < 0.98 * previous_high or long_momentum <= 0:
            return None
        return long_momentum + (close / previous_high - 1)

    # Sector/benchmark histories must be on the same trading days as stock.
    if len(history) < 64 or len(sector) < 64 or len(benchmark) < 64:
        return None
    last_days = [b.day for b in history[-64:]]
    if ([b.day for b in sector[-64:]] != last_days
            or [b.day for b in benchmark[-64:]] != last_days):
        return None
    stock_return = history[-1].close / history[-64].close - 1
    sector_return = sector[-1].close / sector[-64].close - 1
    market_return = benchmark[-1].close / benchmark[-64].close - 1
    if sector_return <= market_return or sector_return <= 0 or stock_return <= sector_return:
        return None
    return (stock_return - sector_return) + (sector_return - market_return)
