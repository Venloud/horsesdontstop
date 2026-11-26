"""Champion/challenger promotion for strategies. Offline research; no orders.

Owner rules (2026-11-20): test every strategy on the recent market; one with a
good win rate over a good number of sessions moves to the top and is used for
at least MIN_HOLD_SESSIONS (no maximum). A champion that is still working is
NOT swapped for a better-looking challenger (no chasing).

Concrete thresholds (proposals, change only with a dated note):
  qualify  : over the last QUALIFY_WINDOW sessions, >= MIN_TRADES closed trades,
             win rate >= MIN_WIN_RATE, profit factor >= MIN_PROFIT_FACTOR,
             net P&L > 0 after costs, max drawdown <= MAX_DRAWDOWN_PCT.
  promote  : when there is no champion, the qualified strategy with the
             highest net P&L in the window.
  keep     : at least MIN_HOLD_SESSIONS; afterwards keep while still working:
             drawdown since promotion <= DEMOTE_DRAWDOWN_PCT and either the
             trailing WORKING_WINDOW P&L >= 0 or it still qualifies.
  demote   : otherwise; promote the best qualified challenger, or none (cash).

simulate() replays the rule day by day using only trades closed before each
day, so its result is a fair walk-forward test of the rule itself.
"""
from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

QUALIFY_WINDOW = 120
MIN_TRADES = 12
MIN_WIN_RATE = 0.55
MIN_PROFIT_FACTOR = 1.3
MAX_DRAWDOWN_PCT = 15.0
MIN_HOLD_SESSIONS = 5
WORKING_WINDOW = 60
DEMOTE_DRAWDOWN_PCT = 10.0


def _day(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def window_stats(trades, curve, t, window):
    """Stats from trades CLOSED in (day[t-window], day[t]] and the equity curve."""
    lo, hi = curve[max(0, t - window)][0], curve[t][0]
    lo, hi = _day(lo), _day(hi)
    closed = [tr for tr in trades if lo < _day(tr["exit_day"]) <= hi]
    wins = [tr["pnl"] for tr in closed if tr["pnl"] > 0]
    losses = [-tr["pnl"] for tr in closed if tr["pnl"] <= 0]
    eq = [e for _, e in curve[max(0, t - window):t + 1]]
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = max(dd, 100 * (1 - e / peak))
    return {"trades": len(closed),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "profit_factor": (sum(wins) / sum(losses)) if losses else (float("inf") if wins else 0.0),
            "net_pnl": round(eq[-1] - eq[0], 2), "max_drawdown_pct": round(dd, 2)}


def qualifies(s) -> bool:
    return (s["trades"] >= MIN_TRADES and s["win_rate"] >= MIN_WIN_RATE
            and s["profit_factor"] >= MIN_PROFIT_FACTOR and s["net_pnl"] > 0
            and s["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT)


def step(state, curves, trades, t):
    """Advance the champion state to day t (decision at the close of t)."""
    champ = state.get("champion")
    if champ:
        held = t - state["since_t"]
        c = curves[champ]
        peak = max(e for _, e in c[state["since_t"]:t + 1])
        dd_since = 100 * (1 - c[t][1] / peak)
        # Working = drawdown since promotion within limit AND either the recent
        # P&L is not negative or the full qualifying record still holds. One
        # soft patch inside a still-qualifying record is not a reason to switch.
        working = dd_since <= DEMOTE_DRAWDOWN_PCT and (
            window_stats(trades[champ], c, t, WORKING_WINDOW)["net_pnl"] >= 0
            or qualifies(window_stats(trades[champ], c, t, QUALIFY_WINDOW)))
        if held < MIN_HOLD_SESSIONS or working:
            return state, None
        reason = f"demoted after {held} sessions (not working: drawdown {dd_since:.1f}%)"
    else:
        reason = "no champion"
    stats = {v: window_stats(trades[v], curves[v], t, QUALIFY_WINDOW) for v in curves}
    qualified = sorted((v for v in curves if qualifies(stats[v]) and v != champ),
                       key=lambda v: (-stats[v]["net_pnl"], v))
    new = qualified[0] if qualified else None
    if new == champ:
        return state, None
    event = {"t": t, "day": str(curves[next(iter(curves))][t][0]), "from": champ, "to": new,
             "why": reason, "stats": stats.get(new) if new else None}
    return {"champion": new, "since_t": t}, event


def simulate(curves: Mapping[str, list], trades: Mapping[str, list], start_t: int,
             start_cash: float = 500.0, switch_cost_bps: float = 60.0) -> dict:
    """Walk-forward: decide at close t, earn the champion's t+1 return (cash if none)."""
    n = min(len(c) for c in curves.values())
    state, eq, events, in_cash_days, path = {"champion": None}, start_cash, [], 0, []
    for t in range(start_t, n - 1):
        state, ev = step(state, curves, trades, t)
        if ev:
            if ev["from"] is not None and ev["to"] is not None:
                eq *= 1 - switch_cost_bps / 10_000
            events.append(ev)
        champ = state["champion"]
        if champ:
            eq *= curves[champ][t + 1][1] / curves[champ][t][1]
        else:
            in_cash_days += 1
        path.append(eq)
    peak, dd = start_cash, 0.0
    for e in path:
        peak = max(peak, e)
        dd = max(dd, 100 * (1 - e / peak))
    return {"from": str(curves[next(iter(curves))][start_t][0]), "final_equity": round(eq, 2),
            "return_pct": round(100 * (eq / start_cash - 1), 2), "max_drawdown_pct": round(dd, 2),
            "promotions": len(events), "days_in_cash": in_cash_days, "days": len(path),
            "current_champion": state["champion"], "events": events}
