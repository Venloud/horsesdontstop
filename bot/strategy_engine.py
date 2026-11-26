"""Researcher + champion selector for the autopilot. Research only; never places orders.

Nightly, after the market recorder:
  1. RESEARCH: build a grid of complete trading systems (entry family and
     parameters x SPY filter x exit rule x number of slots), simulate each as
     the autopilot would trade it (bot.portfolio_sim), and APPROVE only
     systems passing every robustness check:
       - in-sample 2021-2024: CAGR > 0, profit factor >= 1.1, trade t >= 1.5,
         max drawdown <= 35%;
       - both in-sample halves (2021-22, 2023-24) positive;
       - out-of-sample 2025-now (never used for tuning): CAGR > 0;
       - still positive in-sample with 20 bps/side costs;
       - still positive after removing the 10 best trades.
  2. CHAMPION (owner rules 2026-11-20, refined 2026-11-26): among approved
     systems, a challenger qualifies on the last 120 sessions (return > 0,
     profit factor >= 1.1, >= 6 trades, drawdown <= 15%, win rate >= 30%).
     Win rate alone is not the test: trend systems win ~35-40% of trades and
     still make money because winners are bigger than losers. The champion
     is used at least 5 sessions (no maximum) and is NOT swapped while it is
     working (window return >= 0 or still qualifies, and drawdown since
     promotion <= 10%). No qualified system -> cash (no new entries).
  3. Write autopilot-settings.json (the live autopilot reads it every run),
     strategy-engine-state.json and a readable strategy-report.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from .portfolio_sim import System, metrics, simulate

RUNTIME = Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop"
IS_END, OOS_START = date(2024, 12, 31), date(2025, 1, 1)
HALF_SPLIT = date(2022, 12, 31)
WINDOW = 120
MIN_HOLD_SESSIONS = 5
DEMOTE_DRAWDOWN = 0.10

EXITS = {
    "tight": dict(stop=0.03, trail=0.05, take_profit=None, max_sessions=20),
    "medium": dict(stop=0.05, trail=0.08, take_profit=None, max_sessions=40),
    "wide": dict(stop=0.08, trail=0.10, take_profit=None, max_sessions=60),
    "trend": dict(stop=0.10, trail=0.15, take_profit=None, max_sessions=120),
    "swing": dict(stop=0.03, trail=0.05, take_profit=0.06, max_sessions=5),
}
ENTRIES = [
    ("trend_following", ()), ("momentum_volume", (("lookback", 21), ("rel_volume", 1.15))),
    ("high_52_week", ()), ("momentum", (("lookback", 63),)), ("momentum", (("lookback", 126),)),
    ("momentum", (("lookback", 252),)), ("breakout", (("lookback", 55),)),
    ("breakout", (("lookback", 100),)),
]


def candidate_systems(with_insider: bool = False) -> list[System]:
    out = []
    for fam, params in ENTRIES:
        for ex in EXITS.values():
            for flt in (0, 50, 200):
                for slots in (5, 10):
                    out.append(System(fam, params, filter_ma=flt, slots=slots, **ex))
    for flt in (0, 50, 200):
        for ex in (dict(stop=0.05, trail=0.05, take_profit=0.04, max_sessions=5),
                   dict(stop=0.04, trail=0.04, take_profit=0.03, max_sessions=3)):
            for slots in (5, 10):
                out.append(System("pullback", (("days", 3), ("ma", 100)), filter_ma=flt, slots=slots, **ex))
    for ex in ("wide", "trend"):
        out.append(System("ma_trend", (("ma", 200), ("symbols", "SPY+QQQ")), filter_ma=0, slots=2,
                          **EXITS[ex]))
        out.append(System("ma_trend", (("ma", 200), ("symbols", "SPY")), filter_ma=0, slots=1,
                          **EXITS[ex]))
    if with_insider:
        for ex in ("tight", "medium", "wide"):
            for flt in (0, 50):
                for slots in (5, 10):
                    out.append(System("insider", (("min_value", 50000.0), ("valid_sessions", 3)),
                                      filter_ma=flt, slots=slots, **EXITS[ex]))
    return out


def benchmark() -> System:
    """Buy SPY and (practically) never sell: what doing nothing earns."""
    return System("ma_trend", (("ma", 1), ("symbols", "SPY")), filter_ma=0, slots=1,
                  stop=0.99, trail=0.0, take_profit=None, max_sessions=100_000, cash_reserve=0.0)


def _trades_sum_without_best(trades, k=10):
    rets = sorted((t[3] for t in trades), reverse=True)
    return sum(rets[k:])


def research(data_for, insider_events=None, systems=None, log=print) -> list[dict]:
    """data_for(system) -> price data the system trades (insider systems use
    the insider-bought stocks, everything else the main universe)."""
    if isinstance(data_for, dict):
        fixed = data_for
        data_for = lambda s: fixed
    systems = systems or candidate_systems(with_insider=bool(insider_events))
    caches, rows = {}, []
    for i, s in enumerate(systems):
        data = data_for(s)
        spy = data["SPY"]
        cache = caches.setdefault(id(data), {})
        ins = simulate(s, data, end=IS_END, insider_events=insider_events, cache=cache)
        m_is = metrics(ins, spy)
        checks = {}
        checks["in_sample"] = (m_is.get("cagr", -1) > 0 and m_is.get("profit_factor", 0) >= 1.1
                               and m_is.get("t_stat", 0) >= 1.5 and m_is.get("max_drawdown", 1) <= 0.35)
        h1 = metrics(simulate(s, data, end=HALF_SPLIT, insider_events=insider_events, cache=cache))
        h2 = metrics(simulate(s, data, start=HALF_SPLIT, end=IS_END, insider_events=insider_events,
                              cache=cache))
        checks["both_halves"] = h1.get("total_return", -1) > 0 and h2.get("total_return", -1) > 0
        oos = metrics(simulate(s, data, start=OOS_START, insider_events=insider_events, cache=cache), spy)
        checks["out_of_sample"] = oos.get("cagr", -1) > 0
        costly = metrics(simulate(s, data, end=IS_END, cost_bps=20, insider_events=insider_events,
                                  cache=cache))
        checks["high_costs"] = costly.get("cagr", -1) > 0
        checks["without_10_best"] = _trades_sum_without_best(ins.trades) > 0
        rows.append({"system": s.to_dict(), "approved": all(checks.values()), "checks": checks,
                     "in_sample": m_is, "halves": [h1.get("total_return"), h2.get("total_return")],
                     "out_of_sample": oos})
        if (i + 1) % 50 == 0:
            log(f"  researched {i + 1}/{len(systems)}")
    return rows


def window_stats(res, sessions_back=WINDOW) -> dict:
    curve = res.curve
    if len(curve) <= sessions_back:
        return {"return": 0.0, "drawdown": 0.0, "trades": 0, "win_rate": 0.0, "profit_factor": 0.0}
    start_day, start_eq = curve[-sessions_back - 1]
    eq = [e for _, e in curve[-sessions_back - 1:]]
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak)
    tr = [t[3] for t in res.trades if t[1] > start_day]
    wins = [r for r in tr if r > 0]
    losses = [-r for r in tr if r <= 0]
    return {"return": eq[-1] / start_eq - 1, "drawdown": dd, "trades": len(tr),
            "win_rate": len(wins) / len(tr) if tr else 0.0,
            "profit_factor": (sum(wins) / sum(losses)) if losses else (99.0 if wins else 0.0)}


def qualifies(w) -> bool:
    return (w["return"] > 0 and w["profit_factor"] >= 1.1 and w["trades"] >= 6
            and w["drawdown"] <= 0.15 and w["win_rate"] >= 0.30)


def choose_champion(state: dict, approved: list[System], data_for, insider_events=None,
                    today: date | None = None, oos_rank: dict | None = None) -> tuple[dict, dict]:
    """Returns (new_state, decision). Decisions use full-history simulations
    ending at the latest recorded session."""
    if isinstance(data_for, dict):
        fixed = data_for
        data_for = lambda s: fixed
    caches = {}
    sims = {}
    for s in approved:
        d = data_for(s)
        sims[s.id] = simulate(s, d, insider_events=insider_events, cache=caches.setdefault(id(d), {}))
    spy_days = [b.day for b in data_for(System("trend_following"))["SPY"]]
    today = today or spy_days[-1]
    windows = {sid: window_stats(r) for sid, r in sims.items()}
    champ_id = state.get("champion", {}).get("id") if state.get("champion") else None
    decision = {"day": today.isoformat(), "windows": windows}
    if champ_id and state.get("provisional"):
        why = "provisional champion, re-checked nightly"
    elif champ_id:
        since = date.fromisoformat(state["since"])
        held = sum(1 for d in spy_days if d > since)
        decision["held_sessions"] = held
        if champ_id in sims:
            curve = [e for d, e in sims[champ_id].curve if d >= since]
            peak, dd = (curve[0] if curve else 1.0), 0.0
            for e in curve:
                peak = max(peak, e)
                dd = max(dd, 1 - e / peak)
            w = windows[champ_id]
            working = dd <= DEMOTE_DRAWDOWN and (w["return"] >= 0 or qualifies(w))
            decision.update(drawdown_since_promotion=dd, working=working)
            if held < MIN_HOLD_SESSIONS or working:
                decision["action"] = "keep"
                return state, decision
            why = f"not working after {held} sessions (drawdown since promotion {dd:.1%}, window {w['return']:+.1%})"
        else:
            why = "champion no longer passes research"
    else:
        why = "no champion"
    pool = [s for s in approved if s.id != champ_id and qualifies(windows[s.id])]
    pool.sort(key=lambda s: -(windows[s.id]["return"] / max(windows[s.id]["drawdown"], 0.02)))
    new = pool[0] if pool else None
    provisional = False
    if new is None and oos_rank:
        # Nothing qualifies on the recent window: keep testing with the approved
        # system that did best on unseen data, at reduced size (see autopilot_settings).
        ranked = [s for s in approved if s.id in oos_rank]
        if ranked:
            new, provisional = max(ranked, key=lambda s: oos_rank[s.id]), True
            if state.get("provisional") and new.id == champ_id:
                decision.update(action="keep provisional", why=why)
                return state, decision
    decision.update(action=("provisional" if provisional else "switch") if new else "cash", why=why,
                    to=new.id if new else None, from_=champ_id)
    new_state = {"champion": new.to_dict() if new else None, "provisional": provisional,
                 "since": today.isoformat(),
                 "history": (state.get("history") or [])[-50:] + [
                     {"day": today.isoformat(), "from": champ_id, "to": new.id if new else None,
                      "why": why}]}
    return new_state, decision


PROVISIONAL_SIZE = 0.5
# Rough-patch safety net (backtested 2026-11-26): while the champion's own
# track (simulated, same rules) is >= 10% below its peak, new entries are half
# size until it recovers. Full pauses were worse: they miss the rebound.
SAFETY_NET_DRAWDOWN = 0.10
SAFETY_NET_SIZE = 0.5


def safety_net(curve) -> dict:
    eq = [e for _, e in curve]
    if not eq:
        return {"drawdown": 0.0, "active": False}
    dd = 1 - eq[-1] / max(eq)
    return {"drawdown": round(dd, 4), "active": dd >= SAFETY_NET_DRAWDOWN}


def autopilot_settings(champion: dict | None, provisional: bool = False,
                       net: dict | None = None) -> dict:
    from .daily_market_recorder import TRADABLE
    if not champion:
        return {"strategy": "cash", "system_id": "cash (no qualified system)"}
    s = System.from_dict(champion)
    return {"strategy": s.family, "params": dict(s.params),
            "system_id": ("PROVISIONAL " if provisional else "") + s.id,
            "size_scale": (min(PROVISIONAL_SIZE, SAFETY_NET_SIZE)
                           if provisional and (net or {}).get("active")
                           else PROVISIONAL_SIZE if provisional
                           else SAFETY_NET_SIZE if (net or {}).get("active") else 1.0),
            "safety_net": net or {"drawdown": None, "active": False},
            "market_filter_ma": s.filter_ma, "initial_stop": s.stop, "trail": s.trail,
            "take_profit": s.take_profit, "max_sessions": s.max_sessions,
            "max_positions": s.slots, "cash_reserve": s.cash_reserve, "universe": list(TRADABLE)}


def _pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


def report_md(rows, decision, state, bench, live) -> str:
    approved = [r for r in rows if r["approved"]]
    lines = [f"# HorsesDontStop strategy report ({decision['day']})", "",
             f"Researched **{len(rows)}** systems, **{len(approved)}** passed every robustness check "
             f"(with this many tries a few can pass by luck; the universe is today's large caps, "
             f"so history is flattered).", "",
             f"**Champion:** {('PROVISIONAL (half size) ' if state.get('provisional') else '') + state['champion']['id'] if state.get('champion') else 'none, in cash'} "
             f"(decision: {decision.get('action')}; {decision.get('why', '')})", "",
             f"**Benchmark, buy and hold SPY:** in-sample CAGR {_pct(bench['in_sample'].get('cagr'))}, "
             f"out-of-sample {_pct(bench['out_of_sample'].get('cagr'))}, max drawdown "
             f"{bench['in_sample'].get('max_drawdown', 0):.0%}.", ""]
    if live:
        lines += [f"**Live autopilot trades:** {live['trades']} closed, win rate {live['win_rate']:.0%}, "
                  f"total P/L ${live['pnl']:+.2f}.", ""]
    lines += ["## Top approved systems (by out-of-sample CAGR)", "",
              "| System | IS CAGR | IS max DD | OOS CAGR | OOS max DD | trades | win | PF | last 120d |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(approved, key=lambda r: -r["out_of_sample"].get("cagr", -9))[:15]:
        w = decision["windows"].get(r["system"]["id"], {})
        lines.append(f"| `{r['system']['id']}` | {_pct(r['in_sample']['cagr'])} | "
                     f"{r['in_sample']['max_drawdown']:.0%} | {_pct(r['out_of_sample'].get('cagr'))} | "
                     f"{r['out_of_sample'].get('max_drawdown', 0):.0%} | {r['in_sample']['trades']} | "
                     f"{r['in_sample']['win_rate']:.0%} | {r['in_sample']['profit_factor']:.2f} | "
                     f"{_pct(w.get('return'))} |")
    fails = {}
    for r in rows:
        for k, ok in r["checks"].items():
            if not ok:
                fails[k] = fails.get(k, 0) + 1
    lines += ["", "## Why systems failed", ""] + [f"- {k}: {n}" for k, n in sorted(fails.items())]
    return "\n".join(lines) + "\n"


def live_record(folder: Path) -> dict | None:
    path = folder / "autopilot.sqlite"
    if not path.exists():
        return None
    from .autopilot_journal import Journal
    j = Journal(path)
    try:
        tr = j.trades()
    finally:
        j.close()
    if not tr:
        return None
    return {"trades": len(tr), "win_rate": sum(1 for t in tr if (t["pnl"] or 0) > 0) / len(tr),
            "pnl": sum(t["pnl"] or 0 for t in tr)}


def run(folder: Path = RUNTIME, log=print) -> dict:
    from .daily_market_recorder import UNIVERSE, default_folder, read_history
    data = read_history(default_folder(), UNIVERSE)
    insider, ins_data = None, None
    try:
        from .insider_signals import by_symbol, load_events
        from .insider_universe import extend_with_insider_bars
        events = load_events()
        if events:
            ins_data = extend_with_insider_bars(data, events, log=log)
            insider = by_symbol(events)
    except Exception as exc:                       # copy-trading data is optional
        log(f"insider data unavailable: {type(exc).__name__}: {exc}")
    data_for = lambda s: ins_data if (s.family == "insider" and ins_data) else data
    log(f"research on {len(data)} symbols (+{len(ins_data) - len(data) if ins_data else 0} "
        f"insider-bought) through {data['SPY'][-1].day}")
    rows = research(data_for, insider, systems=candidate_systems(with_insider=bool(insider)), log=log)
    approved = [System.from_dict(r["system"]) for r in rows if r["approved"]]
    state_path = folder / "strategy-engine-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    oos_rank = {r["system"]["id"]: r["out_of_sample"].get("cagr", -1) / max(
        r["out_of_sample"].get("max_drawdown", 1), 0.05) for r in rows if r["approved"]}
    state, decision = choose_champion(state, approved, data_for, insider, oos_rank=oos_rank)
    bench_sys = benchmark()
    bench = {"in_sample": metrics(simulate(bench_sys, data, end=IS_END), data["SPY"]),
             "out_of_sample": metrics(simulate(bench_sys, data, start=OOS_START), data["SPY"])}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "strategy-research.json").write_text(json.dumps(
        {"generated_utc": datetime.now(timezone.utc).isoformat(), "rows": rows, "benchmark": bench},
        default=str), encoding="utf-8")
    state_path.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    net = None
    if state.get("champion"):
        champ = System.from_dict(state["champion"])
        net = safety_net(simulate(champ, data_for(champ), insider_events=insider).curve)
        log(f"safety net: champion track {net['drawdown']:.1%} below its peak"
            f" -> {'HALF size' if net['active'] else 'normal size'}")
    settings = autopilot_settings(state.get("champion"), bool(state.get("provisional")), net)
    tmp = folder / "autopilot-settings.tmp"
    tmp.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    os.replace(tmp, folder / "autopilot-settings.json")
    decision_out = {k: v for k, v in decision.items() if k != "windows"}
    (folder / "strategy-report.md").write_text(
        report_md(rows, decision, state, bench, live_record(folder)), encoding="utf-8")
    log(f"STRATEGY ENGINE: {len(approved)}/{len(rows)} approved; decision {decision_out}")
    return {"approved": len(approved), "tested": len(rows), "decision": decision_out,
            "settings": settings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", type=Path, default=RUNTIME)
    a = ap.parse_args(argv)
    run(a.folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
