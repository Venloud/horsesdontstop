"""Daily strategy scoreboard and follow-the-leader test. Offline research; no orders.

Every day after the recorder appends the latest session, this replays every
runnable preregistered variant on the recorded history (same simulator,
$500 start, 1% risk, 30 bps adverse slippage per side, one whole-share long
position) and ranks them by money made over the last 20, 60 and 250 sessions.

It then answers the real question behind "use whichever strategy made the
most money": would SWITCHING each day to the trailing leader have made
money, out of sample? For each day t the leader is chosen from equity known
at the close of t; the selector earns that leader's return on t+1. Each
switch is charged a round trip of slippage. This is the honest test; the raw
"best strategy in hindsight" number is always flattering and is reported
only for comparison.

Limitations: hand-picked surviving universe; IEX daily bars; switching into
a strategy's equity curve assumes its open position could be taken over at
the close (optimistic); etf_dual_momentum (needs verified total-return data)
and overnight_drift (research only) are not run. Exploratory, not a
preregistered confirmatory result.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

from .daily_market_recorder import STOCKS, default_folder, read_history
from .portfolio_research import run_portfolio
from .strategy_adapters import GRID, VariantNotRunnable, adapter

WARMUP = 252
WINDOWS = (20, 60, 250)
LEADER_LOOKBACKS = (20, 60)
RISK, SLIPPAGE_BPS, START_CASH = 0.01, 30.0, 500.0


def runnable_variants():
    out = []
    for v in GRID:
        if v.status != "implemented" or v.candidate == "etf_dual_momentum":
            continue
        try:
            adapter(v.variant_id)
        except (VariantNotRunnable, ValueError):
            continue
        out.append(v.variant_id)
    return out


def first_clean_index(data) -> int:
    """First SPY session after the last day any stock is missing a bar
    (IEX outages), never before the 252-session warmup. Gaps are not filled."""
    spy_days = [b.day for b in data["SPY"]]
    idx = {d: i for i, d in enumerate(spy_days)}
    last_gap = -1
    for s in STOCKS:
        have = {b.day for b in data.get(s, [])}
        for d in spy_days:
            if d not in have:
                last_gap = max(last_gap, idx[d])
    return max(WARMUP, last_gap + 1)


def equity_curves(data: Mapping[str, Sequence], variants: Sequence[str]) -> dict:
    curves, trades = {}, {}
    start = first_clean_index(data)
    for vid in variants:
        report = run_portfolio(data, symbols=STOCKS, risk_fraction=RISK,
                               start_index=start, starting_cash=START_CASH,
                               slippage_bps=SLIPPAGE_BPS, return_equity_curve=True,
                               **adapter(vid))
        curves[vid] = report["equity_curve"]
        trades[vid] = report.get("trades", [])
    return curves, trades


def window_returns(curve, k):
    if len(curve) <= k:
        return None
    a, b = curve[-1 - k][1], curve[-1][1]
    return round(100 * (b / a - 1), 2), round(b - a, 2)


def follow_the_leader(curves: Mapping[str, list], lookback: int,
                      switch_cost_bps: float = 2 * SLIPPAGE_BPS) -> dict:
    """Walk-forward: pick trailing leader at close t, earn its t+1 return."""
    ids = sorted(curves)
    n = min(len(c) for c in curves.values())
    eq = START_CASH
    path, picks, switches, current = [], [], 0, None
    for t in range(lookback, n - 1):
        leader = max(ids, key=lambda v: (curves[v][t][1] / curves[v][t - lookback][1], v))
        if leader != current:
            if current is not None:
                eq *= 1 - switch_cost_bps / 10_000
                switches += 1
            current = leader
        eq *= curves[leader][t + 1][1] / curves[leader][t][1]
        path.append((curves[leader][t + 1][0], round(eq, 2)))
        picks.append(leader)
    first_day = curves[ids[0]][lookback][0] if n > lookback else None
    return {"lookback": lookback, "from": first_day, "final_equity": round(eq, 2),
            "return_pct": round(100 * (eq / START_CASH - 1), 2), "switches": switches,
            "days": len(path), "current_pick": picks[-1] if picks else None,
            "max_drawdown_pct": _max_dd([e for _, e in path])}


def _max_dd(values):
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = max(worst, 100 * (1 - v / peak))
    return round(worst, 2)


def fixed_over_span(curve, start_t):
    a, b = curve[start_t][1], curve[-1][1]
    return round(100 * (b / a - 1), 2)


def build(data) -> dict:
    variants = runnable_variants()
    curves, trades = equity_curves(data, variants)
    spy = data["SPY"]
    last_day = curves[variants[0]][-1][0]
    rows = []
    for vid in variants:
        c = curves[vid]
        row = {"variant": vid, "trades_total": len(trades[vid]),
               "max_drawdown_pct": _max_dd([e for _, e in c])}
        for k in WINDOWS:
            wr = window_returns(c, k)
            row[f"ret_{k}d_pct"], row[f"pnl_{k}d"] = wr if wr else (None, None)
        row["ret_all_pct"] = round(100 * (c[-1][1] / START_CASH - 1), 2)
        rows.append(row)
    rows.sort(key=lambda r: (r["ret_60d_pct"] is None, -(r["ret_60d_pct"] or 0)))
    leaders = {}
    for lb in LEADER_LOOKBACKS:
        ftl = follow_the_leader(curves, lb)
        fixed = {vid: fixed_over_span(curves[vid], lb) for vid in variants}
        best_vid = max(fixed, key=fixed.get)
        ftl["best_fixed_in_hindsight"] = {"variant": best_vid, "return_pct": fixed[best_vid]}
        ftl["median_fixed_return_pct"] = sorted(fixed.values())[len(fixed) // 2]
        spy_start = next(b for b in spy if b.day.isoformat() >= ftl["from"]) if ftl["from"] else None
        ftl["spy_return_pct"] = (round(100 * (spy[-1].close / spy_start.close - 1), 2)
                                 if spy_start else None)
        leaders[f"lookback_{lb}"] = ftl
    from .strategy_promotion import QUALIFY_WINDOW, qualifies, simulate, window_stats
    promo = simulate(curves, trades, QUALIFY_WINDOW)
    t_last = len(curves[variants[0]]) - 1
    for row in rows:
        st = window_stats(trades[row["variant"]], curves[row["variant"]], t_last, QUALIFY_WINDOW)
        row["window_trades"], row["window_win_rate"] = st["trades"], round(st["win_rate"], 2)
        row["window_profit_factor"] = round(st["profit_factor"], 2) if st["profit_factor"] != float("inf") else None
        row["qualifies"] = qualifies(st)
    promotion = {k: v for k, v in promo.items() if k != "events"}
    promotion["recent_events"] = [{k: e[k] for k in ("day", "from", "to", "why")}
                                  for e in promo["events"][-5:]]
    spy_p = next((b for b in spy if b.day.isoformat() >= promotion["from"]), None)
    promotion["spy_return_pct"] = round(100 * (spy[-1].close / spy_p.close - 1), 2) if spy_p else None
    return {"champion": promotion["current_champion"], "promotion": promotion,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(), "through": last_day,
            "sessions_simulated": len(curves[variants[0]]),
            "settings": {"start_cash": START_CASH, "risk": RISK, "slippage_bps": SLIPPAGE_BPS,
                         "universe": list(STOCKS)},
            "today_leader_60d": rows[0]["variant"], "variants": rows,
            "follow_the_leader": leaders,
            "note": "Exploratory research on a hand-picked universe; not a preregistered "
                    "confirmatory result and never an order instruction."}


def render_text(sb) -> str:
    lines = [f"STRATEGY SCOREBOARD through {sb['through']} ({sb['sessions_simulated']} sessions, "
             f"$500 start, 1% risk, 30 bps/side)", "",
             f"{'variant':38} {'20d %':>7} {'60d %':>7} {'250d %':>7} {'all %':>7} {'maxDD':>6} {'trades':>6}"]
    for r in sb["variants"]:
        f = lambda x: "   n/a" if x is None else f"{x:7.2f}"
        lines.append(f"{r['variant']:38} {f(r['ret_20d_pct'])} {f(r['ret_60d_pct'])} "
                     f"{f(r['ret_250d_pct'])} {f(r['ret_all_pct'])} {r['max_drawdown_pct']:6.2f} "
                     f"{r['trades_total']:6d}")
    pr = sb["promotion"]
    lines += ["", f"CHAMPION (promotion rule): {sb['champion'] or 'NONE - no strategy qualifies; stay in cash'}",
              f"  rule backtest from {pr['from']}: {pr['return_pct']}% (maxDD {pr['max_drawdown_pct']}%, "
              f"{pr['promotions']} changes, {pr['days_in_cash']}/{pr['days']} days in cash) | SPY {pr['spy_return_pct']}%",
              "  qualifying now: " + (", ".join(r["variant"] for r in sb["variants"] if r["qualifies"]) or "none")]
    for e in pr["recent_events"]:
        lines.append(f"  {e['day']}: {e['from']} -> {e['to']} ({e['why']})")
    lines.append("")
    for key, l in sb["follow_the_leader"].items():
        lines.append(f"FOLLOW THE LEADER ({key}, from {l['from']}): {l['return_pct']}% "
                     f"({l['switches']} switches, maxDD {l['max_drawdown_pct']}%) | "
                     f"best fixed in hindsight {l['best_fixed_in_hindsight']['variant']} "
                     f"{l['best_fixed_in_hindsight']['return_pct']}% | median fixed "
                     f"{l['median_fixed_return_pct']}% | SPY {l['spy_return_pct']}% | "
                     f"today's pick: {l['current_pick']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, default=default_folder())
    parser.add_argument("--output", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop" / "scoreboard")
    args = parser.parse_args(argv)
    data = read_history(args.folder)
    if len(data.get("SPY", [])) < WARMUP + max(WINDOWS) + 2:
        print("BLOCKED: not enough recorded history; run the recorder first")
        return 2
    sb = build(data)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "latest.json").write_text(json.dumps(sb, indent=2), encoding="utf-8")
    text = render_text(sb)
    (args.output / "latest.txt").write_text(text, encoding="utf-8")
    with open(args.output / "leader-history.csv", "a", encoding="utf-8") as f:
        l20, l60 = sb["follow_the_leader"]["lookback_20"], sb["follow_the_leader"]["lookback_60"]
        f.write(f"{sb['through']},{sb['champion']},{sb['today_leader_60d']},{l20['current_pick']},{l60['current_pick']},"
                f"{l20['return_pct']},{l60['return_pct']}\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
