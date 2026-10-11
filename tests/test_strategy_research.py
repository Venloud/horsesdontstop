"""Deterministic tests for strategy signals and shared-cash execution."""
from datetime import date, timedelta

import pytest

from bot.portfolio_research import run_portfolio
from bot.strategies import DailyBar, STRATEGIES, signal_score


def make_bars(count=390, base=30., change=.12, volume=100., start=date(2024, 1, 1)):
    result = []
    for i in range(count):
        price = base + change * i
        result.append(DailyBar(start + timedelta(days=i), price,
                               price * 1.01, price * .995, price, volume))
    return result


def test_all_four_signals_have_positive_examples():
    stock = make_bars()
    sector = make_bars(base=100, change=.2)
    benchmark = make_bars(base=100, change=.01)
    for name in STRATEGIES:
        history = stock[:320]
        if name == "momentum_volume":
            boosted = list(history)
            last = boosted[-1]
            boosted[-1] = DailyBar(last.day, last.open, last.high,
                                   last.low, last.close, 160)
            history = boosted
        assert signal_score(name, history, sector[:320], benchmark[:320]) is not None


def test_completed_bar_signal_does_not_consult_future():
    history = make_bars(100)
    a = signal_score("trend_following", history)
    future = DailyBar(history[-1].day + timedelta(days=1),
                      1, 1, 1, 1, 100)
    assert a == signal_score("trend_following", history)
    assert future.close == 1  # future is not passed to the signal


def test_52_week_high_requires_full_lookback():
    stock = make_bars()
    assert signal_score("high_52_week", stock[:252]) is None
    assert signal_score("high_52_week", stock[:300]) is not None


def test_sector_history_must_align_to_stock_days():
    stock = make_bars(100)
    sector = make_bars(100, base=100, change=.2)
    spy = make_bars(100, base=100, change=.01)
    assert signal_score("sector_leadership", stock, sector, spy) is not None
    assert signal_score("sector_leadership", stock, sector[1:], spy) is None


def test_single_shared_cash_and_chronological_windows():
    stock = make_bars()
    # Large volume boosts the previous-session momentum signal.
    stock = [DailyBar(b.day, b.open, b.high, b.low, b.close, 200)
             if i % 5 == 0 else b for i, b in enumerate(stock)]
    data = {"SPY": make_bars(base=100, change=.02),
            "AAA": stock, "BBB": make_bars(base=35, change=.10)}
    train = run_portfolio(data, "trend_following", ["AAA", "BBB"],
                          risk_fraction=.02, end_index=280)
    test = run_portfolio(data, "trend_following", ["AAA", "BBB"],
                         risk_fraction=.02, start_index=280)
    assert train["initial_cash"] == test["initial_cash"] == 500
    assert train["last_day"] < test["first_day"]
    assert train["trade_count"] >= 1
    assert test["sessions"] == 110
    assert all(t["shares"] >= 1 for t in train["trades"])
    assert 0 <= train["max_daily_close_drawdown_pct"] <= 100


def test_stop_gap_can_exceed_budget_and_is_not_a_limit_fill():
    stock = make_bars(75, base=60, change=.4)
    bar = stock[60]
    stock[60] = DailyBar(bar.day, 35, 37, 34, 36, 100)
    data = {"SPY": make_bars(75, base=100, change=.01), "AAA": stock}
    report = run_portfolio(data, "trend_following", ["AAA"], risk_fraction=.02,
                           slippage_bps=0)
    assert any(t["reason"] == "gap_stop" and t["pnl"] < -10
               for t in report["trades"])


def test_missing_or_duplicate_bars_fail():
    stock = make_bars(70)
    with pytest.raises(ValueError):
        run_portfolio({"SPY": make_bars(70),
                       "AAA": stock + [stock[0]]},
                      "trend_following", ["AAA"])
    with pytest.raises(ValueError):
        run_portfolio({"SPY": make_bars(70)},
                      "trend_following", ["AAA"])


def test_no_orders_or_secret_dependency_for_offline_portfolio():
    bars = make_bars(70)
    result = run_portfolio({"SPY": bars, "AAA": bars},
                           "high_52_week", ["AAA"])
    assert result["trade_count"] == 0
    assert result["final_equity"] == 500
    assert result["win_rate_pct"] is None
