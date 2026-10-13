"""Offline self-tests for the research simulator (PREREGISTRATION section 7).

Synthetic data only. No network, no Alpaca, no Neon. Nothing here scores a
real strategy; it checks that the harness itself catches look-ahead, prices
random entries at their known cost and recovers a planted edge.

Synthetic bars are "flat": open == high == low == close, so prices move only
between sessions. The simulator then fills every exit at a session open (gap
stop) or the final close, both points on the generated martingale. That makes
the expected gross return of ANY completed-bar entry rule exactly 1.0 by
optional stopping, so the expected net return per trade is known exactly:
(1 - s) / (1 + s) * exp(planted_drift) - 1, with s the per-side slippage.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Callable, Mapping, Sequence

from .portfolio_research import run_portfolio
from .robustness_cli import compare_robustness
from .strategies import DailyBar, STRATEGIES

BASE_VOLUME = 1_000_000.0
SIGNAL_VOLUME = 2_000_000.0


@dataclass(frozen=True)
class PlantedJump:
    """Multiply the next open-to-open move after a signal bar by exp(mu)."""
    symbol: str
    signal_index: int  # completed bar carrying the volume marker
    mu: float


def _weekdays(start: date, count: int) -> list[date]:
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def synthetic_flat_bars(symbols: Sequence[str], sessions: int, seed: int, *,
                        daily_vol: float = 0.02, start_price: float = 40.0,
                        jumps: Sequence[PlantedJump] = (),
                        start: date = date(2010, 1, 4)) -> dict:
    """Multiplicative martingale opens, one flat bar per weekday.

    A PlantedJump at signal_index j marks bar j with SIGNAL_VOLUME (known at
    the close of j), the strategy may enter at open j+1, and the move from
    open j+1 to open j+2 gets an extra exp(mu). Everything else is driftless.
    """
    if sessions < 3 or daily_vol <= 0 or start_price <= 0:
        raise ValueError("invalid synthetic configuration")
    rng = random.Random(seed)
    days = _weekdays(start, sessions)
    planted = {}
    for j in jumps:
        if j.symbol not in symbols or not 0 <= j.signal_index < sessions - 2:
            raise ValueError("planted jump outside the synthetic data")
        planted[(j.symbol, j.signal_index)] = j.mu
    out = {}
    for symbol in symbols:
        price, rows = start_price, []
        for i, day in enumerate(days):
            vol = SIGNAL_VOLUME if (symbol, i) in planted else BASE_VOLUME
            rows.append(DailyBar(day, price, price, price, price, vol))
            # Drift for a signal at i applies to open i+1 -> open i+2.
            drift = planted.get((symbol, i - 1), 0.0)
            z = rng.gauss(0.0, 1.0)
            price *= math.exp(daily_vol * z - daily_vol ** 2 / 2 + drift)
        out[symbol] = rows
    return out


def marker_score_fn(symbol, history, sector_rows, market_rows):
    """Honest planted signal: reads only the last COMPLETED bar's volume."""
    return 1.0 if history and history[-1].volume >= SIGNAL_VOLUME else None


def random_entry_score_fn(seed: int, probability: float) -> Callable:
    """Placebo: eligible with fixed probability, keyed on known data only."""
    if not 0 < probability <= 1:
        raise ValueError("probability must be in (0, 1]")

    def score(symbol, history, sector_rows, market_rows):
        if not history:
            return None
        key = f"{seed}|{symbol}|{history[-1].day.isoformat()}".encode()
        u = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2 ** 64
        return 1.0 - u if u < probability else None
    return score


def expected_net_return(slippage_bps: float, planted_mu: float = 0.0) -> float:
    s = slippage_bps / 10000
    return (1 - s) / (1 + s) * math.exp(planted_mu) - 1


def expected_r(slippage_bps: float, planted_mu: float = 0.0,
               hard_stop_pct: float = 0.03) -> float:
    """1R = entry * hard_stop_pct (the modeled initial stop distance)."""
    return expected_net_return(slippage_bps, planted_mu) / hard_stop_pct


def trade_r_multiples(trades: Sequence[Mapping], hard_stop_pct: float = 0.03):
    return [(t["exit"] - t["entry"]) / (t["entry"] * hard_stop_pct)
            for t in trades]


def mean_ci(values: Sequence[float], z: float = 1.96):
    """Normal-approximation interval for a mean of independent draws."""
    if len(values) < 2:
        raise ValueError("need at least two values")
    m = mean(values)
    se = stdev(values) / math.sqrt(len(values))
    return m, m - z * se, m + z * se, se


# --------------------------------------------------------------- look-ahead

def _perturb_from(data: Mapping[str, Sequence[DailyBar]], cut: date,
                  seed: int) -> dict:
    """Keep everything known before session `cut` plus cut's OPEN.

    The close/high/low/volume of `cut` and every later bar are replaced, so
    any decision that changes before or at the cut-day open used the future.
    """
    rng = random.Random(seed)
    out = {}
    for symbol, rows in data.items():
        new = []
        scale = 1.0
        for b in rows:
            if b.day < cut:
                new.append(b)
            elif b.day == cut:
                c = b.open * math.exp(rng.gauss(0, 0.05))
                hi = max(b.open, c) * (1 + rng.random() * 0.03)
                lo = min(b.open, c) * (1 - rng.random() * 0.03)
                new.append(DailyBar(b.day, b.open, hi, lo, c,
                                    rng.choice((0.0, 1e5, 5e6, 5e7))))
                scale = c / b.close
            else:
                scale *= math.exp(rng.gauss(0, 0.05))
                o = b.open * scale
                c = b.close * scale * math.exp(rng.gauss(0, 0.02))
                hi = max(o, c, b.high * scale)
                lo = min(o, c, b.low * scale)
                new.append(DailyBar(b.day, o, hi, lo, c,
                                    b.volume * rng.choice((0.1, 1.0, 10.0))))
        out[symbol] = new
    return out


def _known_by(result: Mapping, cut: date):
    """Decisions that must be fixed once the cut-day open is known."""
    iso = cut.isoformat()
    entries = [(t["symbol"], t["entry_day"], t["shares"], t["entry"])
               for t in result["trades"] if t["entry_day"] <= iso]
    exits = [(t["symbol"], t["entry_day"], t["exit_day"], t["exit"])
             for t in result["trades"] if t["exit_day"] < iso]
    return entries, exits


def lookahead_audit(data: Mapping[str, Sequence[DailyBar]],
                    run: Callable[[Mapping], Mapping],
                    checkpoints: Sequence[date], seed: int = 0) -> list[dict]:
    """Return a list of violations; empty means no look-ahead was detected.

    `run(data)` must build its signal from the data it is GIVEN (a factory),
    so a signal that peeks at later bars is exposed when they are perturbed.
    Passing is evidence about these checkpoints only, not a proof.
    """
    base = run(data)
    violations = []
    for n, cut in enumerate(checkpoints):
        alt = run(_perturb_from(data, cut, seed * 1000 + n))
        b_entries, b_exits = _known_by(base, cut)
        a_entries, a_exits = _known_by(alt, cut)
        if b_entries != a_entries or b_exits != a_exits:
            violations.append({"cut": cut.isoformat(),
                               "future_information_used": True})
    return violations


# ------------------------------------------------------------ legacy replay

REPLAY_FIELDS = ("sessions", "trade_count", "return_pct", "net_pnl",
                 "final_equity", "max_daily_close_drawdown_pct",
                 "profit_factor", "win_rate_pct", "spy_price_return_pct",
                 "first_day", "last_day", "first_1000_equity_day")

LEDGER_FIELD_MAP = {
    "first_day": "first_day", "last_day": "last_day", "sessions": "sessions",
    "trade_count": "trade_count", "return_pct": "return_pct",
    "net_pnl_usd": "net_pnl", "final_equity_usd": "final_equity",
    "max_daily_close_drawdown_pct": "max_daily_close_drawdown_pct",
    "profit_factor": "profit_factor", "win_rate_pct": "win_rate_pct",
    "spy_price_return_pct": "spy_price_return_pct",
    "first_1000_equity_day": "first_1000_equity_day",
}


def legacy_rows(report: Mapping) -> list[dict]:
    """The 64 window rows then 16 continuous rows, keyed like the ledger."""
    rows = []
    for r in report["runs"]:
        rows.append(dict(r, design="independent_reset_window",
                         window=f"window_{r['window']}"))
    for r in report["continuous_portfolios"]:
        rows.append(dict(r, design="continuous_no_resets", window="full_continuous"))
    return rows


def row_key(row: Mapping):
    return (row["design"], row["window"], row["strategy"],
            float(row["risk_pct"]), float(row["slippage_bps"]))


def _same(a, b, tol: float) -> bool:
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    if isinstance(a, str) or isinstance(b, str):
        try:
            return abs(float(a) - float(b)) <= tol
        except ValueError:
            return str(a) == str(b)
    return abs(float(a) - float(b)) <= tol


def compare_rows(expected: Sequence[Mapping], actual: Sequence[Mapping],
                 tol: float = 0.011, fields=REPLAY_FIELDS,
                 compare_trades: bool = True) -> list[str]:
    """Mismatches between two row sets, to rounding (default one cent/bp)."""
    problems = []
    exp = {row_key(r): r for r in expected}
    act = {row_key(r): r for r in actual}
    if len(exp) != len(expected) or len(act) != len(actual):
        problems.append("duplicate_row_keys")
    for k in sorted(set(exp) ^ set(act), key=str):
        problems.append(f"row_missing:{k}")
    for k in sorted(set(exp) & set(act), key=str):
        for f in fields:
            if not _same(exp[k].get(f), act[k].get(f), tol):
                problems.append(f"{k}:{f}:{exp[k].get(f)}!={act[k].get(f)}")
        if compare_trades and "trades" in exp[k] and "trades" in act[k]:
            et, at = exp[k]["trades"], act[k]["trades"]
            if len(et) != len(at):
                problems.append(f"{k}:trade_count_list")
                continue
            for i, (x, y) in enumerate(zip(et, at)):
                for f in ("symbol", "entry_day", "exit_day", "shares",
                          "reason"):
                    if x[f] != y[f]:
                        problems.append(f"{k}:trade{i}:{f}")
                for f in ("entry", "exit", "pnl"):
                    if not _same(x[f], y[f], tol):
                        problems.append(f"{k}:trade{i}:{f}")
    return problems


def ledger_vs_report(ledger_rows: Sequence[Mapping], report: Mapping,
                     tol: float = 1e-9) -> list[str]:
    """The CSV trial ledger must equal the archived report it came from."""
    mapped = []
    for r in ledger_rows:
        m = {v: r[k] for k, v in LEDGER_FIELD_MAP.items()}
        m.update(design=r["design"], window=r["window"],
                 strategy=r["strategy"], risk_pct=r["risk_pct"],
                 slippage_bps=r["slippage_bps_per_side"])
        mapped.append(m)
    return compare_rows(legacy_rows(report), mapped, tol=tol,
                        compare_trades=False)


def trade_accounting_problems(row: Mapping, tol: float = 0.011) -> list[str]:
    """Re-derive a row's summary from its own trade list (no bars needed).

    Checks the cash chain, count, net P&L, final equity, win rate and profit
    factor. Drawdown and SPY return need daily bars and are NOT checked here.
    """
    out = []
    trades = row["trades"]
    cash = row["initial_cash"]
    for i, t in enumerate(trades):
        if abs(t["entry_cash"] - cash) > tol:
            out.append(f"trade{i}:entry_cash_chain")
        derived = t["shares"] * (t["exit"] - t["entry"])
        if abs(derived - t["pnl"]) > tol + t["shares"] * 1e-4:
            out.append(f"trade{i}:pnl")
        cash = t["exit_cash"]
        if abs(t["entry_cash"] + t["pnl"] - t["exit_cash"]) > 0.02:
            out.append(f"trade{i}:exit_cash")
    if len(trades) != row["trade_count"]:
        out.append("trade_count")
    if abs(cash - row["final_equity"]) > 0.02:
        out.append("final_equity")
    if abs(sum(t["pnl"] for t in trades) - row["net_pnl"]) > 0.02 * max(1, len(trades)):
        out.append("net_pnl")
    if trades:
        wins = sum(t["pnl"] > 0 for t in trades)
        if abs(100 * wins / len(trades) - row["win_rate_pct"]) > tol:
            out.append("win_rate_pct")
        gains = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
        if losses and abs(gains / losses - row["profit_factor"]) > 0.0011:
            out.append("profit_factor")
    return out


def rerun_legacy_report(data: Mapping[str, Sequence[DailyBar]],
                        symbols: Sequence[str], *,
                        continuous_start: int = 260) -> dict:
    """Re-run the legacy 64 window + 16 continuous design, report-shaped.

    Mirrors bot/robustness_cli.main at commit aea2e5b (unchanged on main).
    """
    continuous = []
    for strategy in STRATEGIES:
        for risk in (.01, .02):
            for slip in (10.0, 30.0):
                r = run_portfolio(data, strategy, symbols, risk_fraction=risk,
                                  start_index=continuous_start,
                                  starting_cash=500.0, slippage_bps=slip)
                r["evaluation"] = "continuous_no_resets"
                r["slippage_bps"] = slip
                continuous.append(r)
    return {"selected_symbols": list(symbols),
            "runs": compare_robustness(data, symbols),
            "continuous_portfolios": continuous}


def legacy_replay(data, report: Mapping, symbols: Sequence[str] | None = None):
    """Full replay status and mismatches against the archived report.

    No hash of the original bars was recorded in 2026-10-11, so a refetched
    snapshot is accepted only if it reproduces every row AND every trade.
    """
    if data is None:
        return "blocked_bars_snapshot_missing", []
    symbols = list(symbols or report["selected_symbols"])
    problems = compare_rows(legacy_rows(report),
                            legacy_rows(rerun_legacy_report(data, symbols)))
    return ("replayed_to_rounding" if not problems else "replay_mismatch",
            problems)
