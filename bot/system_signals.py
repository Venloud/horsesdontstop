"""Parametric entry signals shared by the researcher, backtests and the live autopilot.

signal_series(family, params, bars) -> list of score-or-None, one per bar,
using only bars up to and including that bar (decide at the close, buy at the
next open). Higher score = stronger. Same code is used live and in research,
so what was tested is what trades.

The three original families reproduce bot.strategies.signal_score exactly.
"""
from __future__ import annotations

from collections import deque
from math import log10
from typing import Mapping, Sequence

FAMILIES = ("trend_following", "momentum_volume", "high_52_week", "momentum", "breakout",
            "pullback", "ma_trend", "insider")


def _prefix(xs):
    out = [0.0]
    for x in xs:
        out.append(out[-1] + x)
    return out


def _mean(pre, a, b):
    """Mean of xs[a:b] from prefix sums."""
    return (pre[b] - pre[a]) / (b - a)


def _prior_max(xs, n):
    """out[t] = max(xs[t-n:t]) (excludes t), None until n values exist."""
    out, dq = [None] * len(xs), deque()
    for t, x in enumerate(xs):
        while dq and dq[0] < t - n:
            dq.popleft()
        if t >= n:
            out[t] = xs[dq[0]]
        while dq and xs[dq[-1]] <= x:
            dq.pop()
        dq.append(t)
    return out


def _prior_min(xs, n):
    return [None if v is None else -v for v in _prior_max([-x for x in xs], n)]


def signal_series(family: str, params: Mapping | None, bars: Sequence) -> list:
    p = dict(params or {})
    n = len(bars)
    close = [b.close for b in bars]
    pre = _prefix(close)
    out = [None] * n

    if family == "trend_following":
        for t in range(50, n):
            recent = _mean(pre, t - 19, t + 1)
            old = _mean(pre, t - 49, t + 1)
            prior = _mean(pre, t - 20, t)
            c = close[t]
            if c > recent and recent > old and recent > prior:
                out[t] = c / old - 1
        return out

    if family == "momentum_volume":
        lb, rv_min = int(p.get("lookback", 21)), float(p.get("rel_volume", 1.15))
        vol = [b.volume for b in bars]
        vpre = _prefix(vol)
        for t in range(max(lb, 21), n):
            base = _mean(vpre, t - 20, t)
            if base <= 0:
                continue
            rv = vol[t] / base
            mom = close[t] / close[t - lb] - 1
            if mom > 0 and rv >= rv_min:
                out[t] = mom * min(rv, 3.0)
        return out

    if family == "high_52_week":
        prev_high = _prior_max([b.high for b in bars], 252)
        for t in range(252, n):
            ph = prev_high[t]
            mom = close[t] / close[t - 63] - 1
            if close[t] >= 0.98 * ph and mom > 0:
                out[t] = mom + (close[t] / ph - 1)
        return out

    if family == "momentum":
        lb = int(p.get("lookback", 63))
        for t in range(max(lb, 49), n):
            r = close[t] / close[t - lb] - 1
            if r > 0 and close[t] > _mean(pre, t - 49, t + 1):
                out[t] = r
        return out

    if family == "breakout":
        lb = int(p.get("lookback", 20))
        prev_high = _prior_max([b.high for b in bars], lb)
        for t in range(max(lb, 49), n):
            if close[t] > prev_high[t]:
                out[t] = close[t] / _mean(pre, t - 49, t + 1) - 1
        return out

    if family == "pullback":
        ma, days = int(p.get("ma", 100)), int(p.get("days", 3))
        prev_low = _prior_min(close, days)
        for t in range(max(ma - 1, days), n):
            if close[t] > _mean(pre, t - ma + 1, t + 1) and close[t] < prev_low[t]:
                out[t] = close[t - days] / close[t] - 1
        return out

    if family == "ma_trend":
        # Hold while above the N-day average (e.g. SPY/QQQ above 200-day).
        ma = int(p.get("ma", 200))
        for t in range(ma - 1, n):
            avg = _mean(pre, t - ma + 1, t + 1)
            if close[t] > avg:
                out[t] = close[t] / avg - 1
        return out

    if family == "insider":
        # Scores come from insider purchase events, not prices: see insider_scores().
        return out

    raise ValueError(f"unknown signal family {family}")


def latest_score(family: str, params, bars) -> float | None:
    if not bars:
        return None
    return signal_series(family, params, bars)[-1]


def insider_scores(events: Sequence[Mapping], bars: Sequence, *, valid_sessions: int = 3,
                   min_value: float = 50_000.0) -> list:
    """Per-bar score for one symbol from insider purchase events.

    An event counts from its filing day for `valid_sessions` sessions.
    Score = log10(total purchase value) + 0.5 * (distinct insiders - 1),
    only when the total value of open-market purchases in that span reaches
    min_value.
    """
    n = len(bars)
    out = [None] * n
    if not events or not n:
        return out
    days = [b.day for b in bars]
    import bisect
    by_index = {}
    for e in events:
        i = bisect.bisect_left(days, e["filed"])     # first session on/after filing
        if i < n:
            by_index.setdefault(i, []).append(e)
    for t in range(n):
        span = [e for i in range(max(0, t - valid_sessions + 1), t + 1) for e in by_index.get(i, [])]
        if not span:
            continue
        value = sum(e["value"] for e in span)
        owners = {e["owner"] for e in span}
        if value >= min_value:
            out[t] = log10(value) + 0.5 * (len(owners) - 1)
    return out
