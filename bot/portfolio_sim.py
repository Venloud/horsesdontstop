"""Day-by-day portfolio simulation of one trading system, as the autopilot runs it.

Signals are decided at the close of day t-1 and bought at the open of day t,
equal slots of current equity (fractional shares, no margin), at most
`slots` positions. Exits on daily bars, deliberately pessimistic:
gaps through a level fill at the open; if a bar touches both the stop and a
profit level the stop wins; a stop raised by today's high applies tomorrow.
Research only: never places orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from math import sqrt
from statistics import mean, pstdev
from typing import Mapping, Sequence

from .system_signals import insider_scores, signal_series


@dataclass(frozen=True)
class System:
    family: str
    params: tuple = ()                 # (("lookback", 63),)
    filter_ma: int = 50                # SPY above its N-day average (0 = off)
    stop: float = 0.03
    trail: float = 0.05
    take_profit: float | None = None
    max_sessions: int = 20
    slots: int = 5
    cash_reserve: float = 0.02
    # Rough-patch safety net (applies to NEW entries only; open positions keep
    # their stops). Judged on the SHADOW track: what the same system without
    # the brake would have earned, so a pause ends when the strategy itself
    # recovers. brake_ma: shadow equity below its N-day average -> size x
    # brake_scale. brake_dd: shadow drawdown from its peak beyond this ->
    # size x brake_scale.  0 = off.
    brake_ma: int = 0
    brake_dd: float = 0.0
    brake_scale: float = 1.0

    @property
    def id(self) -> str:
        p = ",".join(f"{k}={v}" for k, v in self.params)
        tp = f"tp{self.take_profit:g}" if self.take_profit else "notp"
        brake = ""
        if self.brake_ma:
            brake += f"|brake_ma{self.brake_ma}x{self.brake_scale:g}"
        if self.brake_dd:
            brake += f"|brake_dd{self.brake_dd:g}x{self.brake_scale:g}"
        return (f"{self.family}({p})|spy{self.filter_ma}|stop{self.stop:g}|trail{self.trail:g}"
                f"|{tp}|{self.max_sessions}s|{self.slots}slots{brake}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["params"] = dict(self.params)
        d["id"] = self.id
        return d

    @staticmethod
    def from_dict(d: Mapping) -> "System":
        kw = {k: d[k] for k in System.__dataclass_fields__ if k in d}
        kw["params"] = tuple(sorted(dict(d.get("params") or {}).items()))
        return System(**kw)


@dataclass
class SimResult:
    system: System
    curve: list = field(default_factory=list)      # [(day, equity)]
    trades: list = field(default_factory=list)     # [(entry_day, exit_day, symbol, return, reason)]


def _spy_ok(spy, n):
    if n <= 0:
        return [True] * len(spy)
    out, run = [False] * len(spy), 0.0
    closes = [b.close for b in spy]
    for i, c in enumerate(closes):
        run += c
        if i >= n:
            run -= closes[i - n]
        if i >= n - 1:
            out[i] = c > run / n
    return out


def score_table(system: System, data: Mapping[str, Sequence], insider_events=None,
                cache: dict | None = None) -> dict:
    """{symbol: {day: score}} for the system's signal family (cached by family+params)."""
    key = (system.family, system.params)
    if cache is not None and key in cache:
        return cache[key]
    table = {}
    params = dict(system.params)
    only = set(str(params.pop("symbols")).split("+")) if "symbols" in params else None
    for sym, bars in data.items():
        if not bars or (only is not None and sym not in only):
            continue
        if system.family == "insider":
            series = insider_scores((insider_events or {}).get(sym, []), bars, **params)
        else:
            series = signal_series(system.family, params, bars)
        table[sym] = {b.day: s for b, s in zip(bars, series) if s is not None}
    if cache is not None:
        cache[key] = table
    return table


def simulate(system: System, data: Mapping[str, Sequence], *, start=None, end=None,
             cost_bps: float = 5.0, insider_events=None, cache: dict | None = None) -> SimResult:
    spy = data["SPY"]
    days = [b.day for b in spy]
    ok = dict(zip(days, _spy_ok(spy, system.filter_ma)))
    scores = score_table(system, data, insider_events, cache)
    bar_at = {s: {b.day: b for b in bars} for s, bars in data.items()}
    cost = cost_bps / 10_000
    res = SimResult(system)
    shadow = None
    if system.brake_ma or system.brake_dd:
        base = replace(system, brake_ma=0, brake_dd=0.0, brake_scale=1.0)
        shadow = simulate(base, data, start=start, end=end, cost_bps=cost_bps,
                          insider_events=insider_events, cache=cache).curve
    shadow_upto = []
    cash, equity = 1.0, 1.0
    pos: dict[str, dict] = {}
    last_close: dict[str, float] = {}
    idx = [i for i, d in enumerate(days) if (start is None or d >= start) and (end is None or d <= end)]
    for i in idx:
        if i == 0:
            continue
        d, prev = days[i], days[i - 1]
        # --- entries at today's open from yesterday's signals
        free = system.slots - len(pos)
        if shadow is not None:
            while len(shadow_upto) < len(shadow) and shadow[len(shadow_upto)][0] < d:
                shadow_upto.append(shadow[len(shadow_upto)])
        scale = brake_scale(system, shadow_upto)
        if free > 0 and ok.get(prev) and scale > 0:
            ranked = sorted(((sc[prev], s) for s, sc in scores.items()
                             if prev in sc and s not in pos and d in bar_at[s]), reverse=True)
            for _, sym in ranked[:free]:
                budget = min(equity * (1 - system.cash_reserve) / system.slots * scale,
                             cash - equity * system.cash_reserve)
                if budget <= equity * 0.01:
                    break
                px = bar_at[sym][d].open
                qty = budget / (px * (1 + cost))
                cash -= qty * px * (1 + cost)
                pos[sym] = {"qty": qty, "entry": px, "stop": px * (1 - system.stop),
                            "high": px, "n": 0, "day": d}
        # --- manage positions on today's bar
        for sym in list(pos):
            b = bar_at[sym].get(d)
            if b is None:
                continue
            p = pos[sym]
            p["n"] += 1
            tp = p["entry"] * (1 + system.take_profit) if system.take_profit else None
            exit_px = reason = None
            if b.open <= p["stop"]:
                exit_px, reason = b.open, "gap stop"
            elif tp and b.open >= tp:
                exit_px, reason = b.open, "gap take-profit"
            elif b.low <= p["stop"]:
                exit_px, reason = p["stop"], "stop"
            elif tp and b.high >= tp:
                exit_px, reason = tp, "take-profit"
            elif p["n"] >= system.max_sessions:
                exit_px, reason = b.close, "time limit"
            if exit_px is not None:
                cash += p["qty"] * exit_px * (1 - cost)
                res.trades.append((p["day"], d, sym, exit_px / p["entry"] * (1 - cost) / (1 + cost) - 1,
                                   reason))
                del pos[sym]
                continue
            p["high"] = max(p["high"], b.high)
            if system.trail:
                p["stop"] = max(p["stop"], p["high"] * (1 - system.trail))
        for sym in pos:
            b = bar_at[sym].get(d)
            if b is not None:
                last_close[sym] = b.close
        equity = cash + sum(p["qty"] * last_close.get(s, p["entry"]) for s, p in pos.items())
        res.curve.append((d, equity))
    return res


def brake_scale(system: System, curve) -> float:
    """Size multiplier for new entries from the system's own equity so far."""
    if not (system.brake_ma or system.brake_dd) or not curve:
        return 1.0
    eq = [e for _, e in curve]
    hit = False
    if system.brake_ma and len(eq) >= system.brake_ma:
        hit = eq[-1] < sum(eq[-system.brake_ma:]) / system.brake_ma
    if system.brake_dd and eq[-1] < max(eq) * (1 - system.brake_dd):
        hit = True
    return system.brake_scale if hit else 1.0


def max_drawdown(values) -> float:
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst


def metrics(res: SimResult, spy: Sequence | None = None) -> dict:
    curve = res.curve
    if len(curve) < 2:
        return {"days": len(curve), "trades": 0}
    eq = [e for _, e in curve]
    years = max((curve[-1][0] - curve[0][0]).days / 365.25, 1 / 365.25)
    total = eq[-1] / eq[0] - 1
    daily = [b / a - 1 for a, b in zip(eq, eq[1:])]
    sd = pstdev(daily) if len(daily) > 1 else 0.0
    rets = [t[3] for t in res.trades]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r <= 0]
    out = {
        "from": curve[0][0].isoformat(), "to": curve[-1][0].isoformat(), "days": len(curve),
        "total_return": total, "cagr": (eq[-1] / eq[0]) ** (1 / years) - 1,
        "max_drawdown": max_drawdown(eq), "sharpe": (mean(daily) / sd * sqrt(252)) if sd else 0.0,
        "trades": len(rets), "win_rate": len(wins) / len(rets) if rets else 0.0,
        "avg_trade": mean(rets) if rets else 0.0,
        "profit_factor": (sum(wins) / sum(losses)) if losses else (float("inf") if wins else 0.0),
        "t_stat": (mean(rets) / (pstdev(rets) / sqrt(len(rets)))) if len(rets) > 2 and pstdev(rets) else 0.0,
        "exposure": None,
    }
    if spy:
        s = [b for b in spy if curve[0][0] <= b.day <= curve[-1][0]]
        if len(s) > 1:
            out["spy_total_return"] = s[-1].close / s[0].close - 1
            out["spy_cagr"] = (s[-1].close / s[0].close) ** (1 / years) - 1
            out["spy_max_drawdown"] = max_drawdown([b.close for b in s])
    return out
