"""Adapter tests: synthetic data only. No network, no orders."""
import math
import random
from collections import Counter
from datetime import date, timedelta

import pytest

from bot.phase2_research_signals import SECTOR_ETF_UNIVERSE, TotalReturnPoint
from bot.portfolio_research import run_portfolio
from bot.research_harness import lookahead_audit
from bot.strategies import DailyBar
from bot.strategies import signal_score
from bot.strategy_adapters import (
    DEFAULT_PARAMS, GRID, VARIANTS, VariantNotRunnable, _generic_score, adapter,
    etf_first_eligible_rebalance, month_start_sessions,
)

STOCKS = ["AAPL", "JPM", "MSFT", "BAC"]
VERIFIED = {"total_return": True, "universe": True, "calendar": True}


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars(symbols, n, seed, vol=0.015, start=date(2015, 1, 5)):
    rng = random.Random(seed)
    days = weekdays(start, n)
    out = {}
    for s in symbols:
        price, rows = 30 + 20 * rng.random(), []
        drift = rng.uniform(0.0, 0.0015)
        for d in days:
            o = price * math.exp(rng.gauss(0, 0.008))
            c = o * math.exp(rng.gauss(drift, vol))
            rows.append(DailyBar(d, o, max(o, c) * (1 + rng.random() * 0.01),
                                 min(o, c) * (1 - rng.random() * 0.01), c,
                                 1e6 * math.exp(rng.gauss(0, 0.4))))
            price = c
        out[s] = rows
    return out


def total_return_from(data):
    return {s: [TotalReturnPoint(b.day, b.close) for b in data[s]]
            for s in SECTOR_ETF_UNIVERSE}


# ------------------------------------------------------------------ grid

def test_grid_is_the_frozen_21_variants():
    assert len(GRID) == len(VARIANTS) == 21
    per = Counter(v.candidate for v in GRID)
    assert per == {"momentum_volume": 4, "trend_following": 3, "high_52_week": 3,
                   "sector_leadership": 2, "short_term_reversal": 4,
                   "etf_dual_momentum": 3, "overnight_drift": 2}
    status = Counter(v.status for v in GRID)
    assert status == {"implemented": 19, "research_only": 2}


@pytest.mark.parametrize("vid", [v.variant_id for v in GRID if v.status != "implemented"])
def test_research_only_variants_refuse_to_run(vid):
    with pytest.raises(VariantNotRunnable):
        adapter(vid)


def test_unknown_variant_rejected():
    with pytest.raises(ValueError):
        adapter("momentum_volume:lb99_rv9.99")


# --------------------------------------------------- existing 4 defaults

@pytest.mark.parametrize("vid", [
    "momentum_volume:lb21_rv1.15", "trend_following:sma20_50",
    "high_52_week:within2pct", "sector_leadership:rs63"])
def test_default_variants_equal_the_legacy_simulation(vid):
    data = bars(STOCKS + ["XLK", "XLF", "SPY"], 360, 3)
    kw = adapter(vid)
    legacy = run_portfolio(data, kw["strategy"], STOCKS, start_index=60)
    assert run_portfolio(data, symbols=STOCKS, start_index=60, **kw) == legacy


# ---------------------------------------------------------- reversal

def reversal_data():
    return bars(STOCKS + ["SPY"], 520, 12, vol=0.02)


@pytest.mark.parametrize("vid", [v.variant_id for v in GRID
                                 if v.candidate == "short_term_reversal"])
def test_reversal_exits_by_max_hold_or_stop(vid):
    data = reversal_data()
    kw = adapter(vid)
    hold = kw["max_hold_sessions"]
    res = run_portfolio(data, symbols=STOCKS, start_index=210, **kw)
    assert res["trade_count"] > 0
    days = {b.day.isoformat(): i for i, b in enumerate(data["SPY"])}
    reasons = Counter(t["reason"] for t in res["trades"])
    assert reasons["max_hold"] > 0
    assert set(reasons) <= {"max_hold", "gap_stop", "stop", "entry_day_stop",
                            "end_of_window"}
    for t in res["trades"]:
        held = days[t["exit_day"]] - days[t["entry_day"]]
        assert held <= hold
        if t["reason"] == "max_hold":
            assert held == hold


@pytest.mark.parametrize("vid", ["short_term_reversal:rsi10_hold1",
                                 "short_term_reversal:rsi5_hold3"])
def test_reversal_passes_lookahead_audit(vid):
    data = reversal_data()
    kw = adapter(vid)

    def run(d):
        return run_portfolio(d, symbols=STOCKS, start_index=210, **kw)
    base = run(data)
    assert base["trade_count"] > 0
    days = [b.day for b in data["SPY"]]
    # Every actual entry day plus a sparse grid of no-entry days.
    entries = {date.fromisoformat(t["entry_day"]) for t in base["trades"]}
    cuts = sorted(entries | set(days[220:510:9]))
    assert lookahead_audit(data, run, cuts, seed=4) == []


def test_max_hold_requires_positive_int():
    data = bars(["AAA", "SPY"], 30, 1)
    with pytest.raises(ValueError):
        run_portfolio(data, "x", ["AAA"], score_fn=lambda *a: 1.0, max_hold_sessions=0)


# ------------------------------------------------------- ETF momentum

def etf_data(seed=5, n=420):
    return bars(list(SECTOR_ETF_UNIVERSE), n, seed)


def etf_run(data, vid="etf_dual_momentum:lb63", stops=False, verified=VERIFIED):
    sessions = [b.day for b in data["SPY"]]
    kw = adapter(vid, sessions=sessions, total_return=total_return_from(data),
                 verified=verified, etf_use_standard_stops=stops)
    return run_portfolio(data, symbols=list(SECTOR_ETF_UNIVERSE), **kw), sessions


def test_etf_rotates_only_at_month_starts_and_holds_the_leader():
    data = etf_data()
    res, sessions = etf_run(data)
    assert res["trade_count"] >= 5
    starts = {d.isoformat() for d in month_start_sessions(sessions)}
    for t in res["trades"]:
        assert t["entry_day"] in starts
        assert t["reason"] in ("rebalance", "end_of_window")
    # The held ETF is the one with the best trailing 63-session TR return.
    pos = {d.isoformat(): i for i, d in enumerate(sessions)}
    for t in res["trades"]:
        i = pos[t["entry_day"]] - 1
        rets = {s: data[s][i].close / data[s][i - 63].close - 1
                for s in SECTOR_ETF_UNIVERSE}
        best = min(rets, key=lambda s: (-rets[s], s))
        assert t["symbol"] == best and rets[best] > 0


def test_etf_rotation_reenters_at_the_same_open():
    res, _ = etf_run(etf_data())
    trades = res["trades"]
    same_day = [a for a, b in zip(trades, trades[1:])
                if a["reason"] == "rebalance" and b["entry_day"] == a["exit_day"]]
    assert same_day, "a rebalance must be able to buy at the open it sold"


def test_etf_with_standard_stops_can_stop_out_between_rebalances():
    res, _ = etf_run(etf_data(seed=8), stops=True)
    reasons = Counter(t["reason"] for t in res["trades"])
    assert reasons["gap_stop"] + reasons["stop"] + reasons["entry_day_stop"] > 0


def test_etf_without_verified_inputs_never_trades():
    unverified = dict(VERIFIED, total_return=False)
    res, _ = etf_run(etf_data(), verified=unverified)
    assert res["trade_count"] == 0


def test_etf_requires_explicit_stop_choice_and_inputs():
    data = etf_data(n=80)
    sessions = [b.day for b in data["SPY"]]
    with pytest.raises(ValueError):
        adapter("etf_dual_momentum:lb63", sessions=sessions,
                total_return=total_return_from(data), verified=VERIFIED)
    with pytest.raises(ValueError):
        adapter("etf_dual_momentum:lb63", etf_use_standard_stops=False)


def test_etf_passes_lookahead_audit():
    data = etf_data()
    sessions = [b.day for b in data["SPY"]]

    def run(d):
        kw = adapter("etf_dual_momentum:lb63", sessions=sessions,
                     total_return=total_return_from(d), verified=VERIFIED,
                     etf_use_standard_stops=False)
        return run_portfolio(d, symbols=list(SECTOR_ETF_UNIVERSE), **kw)
    assert run(data)["trade_count"] > 0
    starts = sorted(month_start_sessions(sessions))
    # Cut on every month start and the session before it.
    cuts = sorted({c for d in starts for c in (d, sessions[sessions.index(d) - 1])})
    assert lookahead_audit(data, run, cuts, seed=2) == []


def test_late_etf_listing_blocks_all_selection_until_it_has_history():
    data = etf_data(n=300)
    late = data["XLC"][200:]  # XLC "lists" at session 200
    tr = total_return_from(data)
    tr["XLC"] = [TotalReturnPoint(b.day, b.close) for b in late]
    sessions = [b.day for b in data["SPY"]]
    kw = adapter("etf_dual_momentum:lb63", sessions=sessions, total_return=tr,
                 verified=VERIFIED, etf_use_standard_stops=False)
    res = run_portfolio(data, symbols=list(SECTOR_ETF_UNIVERSE), **kw)
    first = min(t["entry_day"] for t in res["trades"])
    assert first > sessions[200 + 63].isoformat()


def test_xlc_listing_caps_usable_etf_history_below_ten_years():
    # Approximate first trading days; verify against the frozen data vendor.
    first = {s: date(2005, 1, 3) for s in SECTOR_ETF_UNIVERSE}
    first["XLRE"] = date(2015, 10, 8)
    first["XLC"] = date(2018, 6, 19)
    sessions = weekdays(date(2004, 1, 5), 6000)  # weekday proxy calendar
    sessions = [d for d in sessions if d <= date(2026, 10, 9)]
    asof = date(2026, 10, 9)
    for lookback, latest_start in ((63, date(2018, 10, 1)),
                                   (126, date(2019, 1, 2)),
                                   (252, date(2019, 7, 1))):
        start = etf_first_eligible_rebalance(first, sessions, lookback)
        assert start is not None and start <= latest_start
        years = (asof - start).days / 365.25
        assert years < 10, "all three ETF variants fall short of 10 years"


# ------------------------- non-default variants of the 4 coded strategies

CODED = ("momentum_volume", "trend_following", "high_52_week", "sector_leadership")


@pytest.mark.parametrize("candidate", CODED)
def test_generic_score_equals_signal_score_at_default_parameters(candidate):
    data = bars(["AAPL", "XLK", "SPY"], 400, 21, vol=0.02)
    stock, sector, market = data["AAPL"], data["XLK"], data["SPY"]
    generic = _generic_score(candidate, dict(DEFAULT_PARAMS[candidate]))
    seen = 0
    for i in range(30, len(stock) + 1):
        a = signal_score(candidate, stock[:i], sector[:i], market[:i])
        b = generic("AAPL", stock[:i], sector[:i], market[:i])
        assert a == b, (candidate, i)
        seen += a is not None
    assert seen > 0, "equivalence test never saw an eligible signal"


@pytest.mark.parametrize("vid", [v.variant_id for v in GRID
                                 if v.candidate in CODED])
def test_every_coded_variant_runs_and_passes_lookahead_audit(vid):
    data = bars(STOCKS + ["XLK", "XLF", "SPY"], 330, 5, vol=0.02)
    kw = adapter(vid)

    def run(d):
        return run_portfolio(d, symbols=STOCKS, start_index=260, **kw)
    base = run(data)
    assert base["trade_count"] > 0
    days = [b.day for b in data["SPY"]]
    entries = {date.fromisoformat(t["entry_day"]) for t in base["trades"]}
    cuts = sorted(entries | set(days[265:325:12]))
    assert lookahead_audit(data, run, cuts, seed=2) == []


def test_looser_threshold_never_rejects_what_the_tighter_one_accepts():
    data = bars(["AAPL"], 400, 8, vol=0.02)["AAPL"]
    tight = _generic_score("high_52_week", {"within_pct": 1})
    mid = _generic_score("high_52_week", {"within_pct": 2})
    loose = _generic_score("high_52_week", {"within_pct": 5})
    for i in range(260, 400):
        h = data[:i]
        if tight("A", h, [], []) is not None:
            assert mid("A", h, [], []) is not None
        if mid("A", h, [], []) is not None:
            assert loose("A", h, [], []) is not None
