"""Compare exit rules on the same entries (research only, never trades).

Entries: momentum_volume signals on recorded daily bars (IEX), bought at the
next session's open, one open trade per symbol. Each rule then manages the
trade on daily bars. Daily bars hide the intraday path, so the simulation is
deliberately pessimistic:
  * a gap through a level fills at the open;
  * if a bar touches both the stop and a profit level, the stop wins;
  * a stop raised by today's new high only applies from the next session.

Usage:  python -m bot.exit_rule_backtest [--history DIR] [--cost-bps 5]
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

from .strategies import DailyBar, signal_score


@dataclass(frozen=True)
class Rule:
    name: str
    stop_pct: float = 0.03          # initial stop below entry
    take_profit: float | None = 0.06
    trail: float | None = 0.05      # trail below high water (current live rule)
    lock_at: float | None = None    # profit-lock: activates at this gain
    lock_floor: float = 0.0         # ...stop jumps to entry*(1+lock_floor)
    lock_trail: float = 0.015       # ...then trails this far below the high
    max_sessions: int = 5


RULES = (
    Rule("current: TP +6%, trail 5%, 5 sessions"),
    Rule("lock +6%->+4.5%, trail 1.5%, 5 sessions", take_profit=None, trail=0.05,
         lock_at=0.06, lock_floor=0.045, lock_trail=0.015),
    Rule("lock +6%->+4.5%, trail 1.5%, 20 sessions", take_profit=None, trail=0.05,
         lock_at=0.06, lock_floor=0.045, lock_trail=0.015, max_sessions=20),
    Rule("lock +6%->+5%, trail 1%, 20 sessions", take_profit=None, trail=0.05,
         lock_at=0.06, lock_floor=0.05, lock_trail=0.01, max_sessions=20),
    Rule("lock +6%->+4%, trail 2.5%, 20 sessions", take_profit=None, trail=0.05,
         lock_at=0.06, lock_floor=0.04, lock_trail=0.025, max_sessions=20),
    Rule("lock +4%->+2.5%, trail 2%, 20 sessions", take_profit=None, trail=0.05,
         lock_at=0.04, lock_floor=0.025, lock_trail=0.02, max_sessions=20),
    Rule("user exact: lock +6%->+6%, trail 0.5%, 20 sessions", take_profit=None,
         trail=0.05, lock_at=0.06, lock_floor=0.06, lock_trail=0.005, max_sessions=20),
)


def load(folder: Path) -> dict[str, list[tuple[str, DailyBar]]]:
    data = {}
    for f in sorted(folder.glob("*.csv")):
        rows = []
        with open(f, newline="", encoding="utf-8") as h:
            for r in csv.DictReader(h):
                try:
                    rows.append((r["day"], DailyBar(date.fromisoformat(r["day"]), float(r["open"]), float(r["high"]),
                                                    float(r["low"]), float(r["close"]),
                                                    float(r["volume"]))))
                except (ValueError, KeyError):
                    continue
        data[f.stem] = rows
    return data


def simulate(bars: list[DailyBar], i: int, rule: Rule):
    """Enter at bars[i].open. Returns (gross return, sessions, reason)."""
    entry = bars[i].open
    stop = entry * (1 - rule.stop_pct)
    high = entry
    locked = False
    for k in range(rule.max_sessions):
        j = i + k
        if j >= len(bars):
            return None
        b = bars[j]
        tp = entry * (1 + rule.take_profit) if rule.take_profit else None
        if b.open <= stop:
            return b.open / entry - 1, k + 1, "gap stop"
        if tp and b.open >= tp:
            return b.open / entry - 1, k + 1, "gap take-profit"
        if b.low <= stop:
            return stop / entry - 1, k + 1, "locked stop" if locked else "stop"
        if tp and b.high >= tp:
            return rule.take_profit, k + 1, "take-profit"
        # End of session: update high water and raise the stop for tomorrow.
        high = max(high, b.high)
        if rule.trail:
            stop = max(stop, high * (1 - rule.trail))
        if rule.lock_at is not None and high >= entry * (1 + rule.lock_at):
            locked = True
            stop = max(stop, entry * (1 + rule.lock_floor), high * (1 - rule.lock_trail))
    last = bars[i + rule.max_sessions - 1]
    return last.close / entry - 1, rule.max_sessions, "time limit"


def entries(data):
    """(symbol, entry index) for every momentum_volume signal, next-open entry."""
    out = []
    for sym, rows in data.items():
        bars = [b for _, b in rows]
        for t in range(22, len(bars) - 1):
            if signal_score("momentum_volume", bars[: t + 1]) is not None:
                out.append((sym, t + 1))
    return out


def run(data, rules=RULES, cost_bps=5.0):
    sigs = entries(data)
    cost = 2 * cost_bps / 10_000
    results = {}
    for rule in rules:
        trades, busy_until = [], {}
        for sym, i in sorted(sigs, key=lambda s: (s[1], s[0])):
            if i <= busy_until.get(sym, -1):
                continue      # one open trade per symbol
            bars = [b for _, b in data[sym]]
            r = simulate(bars, i, rule)
            if r is None:
                continue
            ret, held, reason = r
            busy_until[sym] = i + held - 1
            trades.append((data[sym][i][0], sym, ret - cost, held, reason))
        results[rule.name] = trades
    return results


def summary(trades):
    rets = [t[2] for t in trades]
    if not rets:
        return {}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    reasons = {}
    for t in trades:
        reasons[t[4]] = reasons.get(t[4], 0) + 1
    half = len(trades) // 2
    return {"trades": len(rets), "win_rate": len(wins) / len(rets), "avg": mean(rets),
            "median": median(rets), "avg_win": mean(wins) if wins else 0.0,
            "avg_loss": mean(losses) if losses else 0.0, "total": sum(rets),
            "best": max(rets), "first_half_avg": mean(rets[:half]) if half else 0.0,
            "second_half_avg": mean(rets[half:]), "avg_held": mean(t[3] for t in trades),
            "per_session": sum(rets) / sum(t[3] for t in trades), "reasons": reasons}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--history", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "."))
                   / "horsesdontstop" / "market-history")
    p.add_argument("--cost-bps", type=float, default=5.0, help="cost per side, bps")
    a = p.parse_args(argv)
    data = load(a.history)
    for name, trades in run(data, cost_bps=a.cost_bps).items():
        s = summary(trades)
        print(f"\n{name}\n  trades {s['trades']}  win {s['win_rate']:.0%}  avg {s['avg']:+.2%}"
              f"  median {s['median']:+.2%}  avg win {s['avg_win']:+.2%}  avg loss {s['avg_loss']:+.2%}"
              f"\n  sum {s['total']:+.1%}  per session held {s['per_session']:+.3%}  avg held {s['avg_held']:.1f}"
              f"  best {s['best']:+.1%}  halves {s['first_half_avg']:+.2%} / {s['second_half_avg']:+.2%}"
              f"\n  exits {s['reasons']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
