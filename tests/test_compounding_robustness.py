"""Audit realistic immediately compounded research returns and robust window design."""
from datetime import date, timedelta

import pytest

from bot.portfolio_research import run_portfolio
from bot.robustness_cli import evaluation_windows
from bot.strategies import DailyBar


def day_bar(day, o, h, l, c):
    return DailyBar(day, o, h, l, c, 1000)


def compounding_fixture(loss=False):
    start = date(2024, 1, 1)
    stock = []
    market = []
    for i in range(80):
        day = start + timedelta(days=i)
        market.append(day_bar(day, 100, 101, 99, 100))
        if i < 60 or i % 3 == 0:
            o, h, l, c = 40., 41., 39.5, 40.
        elif i % 3 == 1:
            o, h, l, c = 50., 52., 49., 50.
        else:
            o, h, l, c = 50., 51., 48., 50.
        if loss and i == 61:
            o, h, l, c = 30., 31., 29., 30.
        stock.append(day_bar(day, o, h, l, c))
    return {"SPY": market, "AAA": stock}


def test_compound_wins_immediately_below_1000(monkeypatch):
    # Isolate accounting from strategy thresholds. Force deterministic entries
    # only after 60 previous observations, with two positive realized exits.
    monkeypatch.setattr("bot.portfolio_research.signal_score",
                        lambda strategy, history, *args:
                        1.0 if len(history) >= 60 else None)
    result = run_portfolio(compounding_fixture(), "trend_following",
                           ["AAA"], slippage_bps=0, risk_fraction=.02)
    trades = result["trades"]
    assert len(trades) >= 2
    assert trades[0]["entry_cash"] == 500
    assert trades[0]["exit_cash"] > 500
    assert trades[1]["entry_cash"] == trades[0]["exit_cash"]
    assert trades[1]["shares"] > trades[0]["shares"]
    assert trades[1]["risk_budget"] > trades[0]["risk_budget"]
    assert result["reinvest_realized_profits_from_first_trade"] is True
    assert trades[1]["entry_cash"] < 1000  # reinvest BEFORE any $1K milestone


def test_losses_lower_next_risk_budget(monkeypatch):
    monkeypatch.setattr("bot.portfolio_research.signal_score",
                        lambda strategy, history, *args:
                        1.0 if len(history) >= 60 else None)
    result = run_portfolio(compounding_fixture(loss=True), "trend_following",
                           ["AAA"], slippage_bps=0, risk_fraction=.02)
    trades = result["trades"]
    assert trades[0]["reason"] == "gap_stop"
    assert trades[0]["exit_cash"] < 500
    assert trades[1]["entry_cash"] == trades[0]["exit_cash"]
    assert trades[1]["risk_budget"] < trades[0]["risk_budget"]


def test_windows_are_nonoverlapping_and_have_warmup():
    windows = evaluation_windows(760, count=4, width=105)
    assert len(windows) == 4
    assert windows[0][0] >= 260
    assert windows[-1][1] == 760
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_not_enough_warmup_fails():
    with pytest.raises(ValueError):
        evaluation_windows(600, count=4, width=105)


def test_no_real_trades_or_contributions_when_no_signal():
    bars = [day_bar(date(2024, 1, 1) + timedelta(days=i),
                    100, 101, 99, 100) for i in range(75)]
    result = run_portfolio({"SPY": bars, "AAA": bars}, "high_52_week",
                           ["AAA"], starting_cash=500)
    assert result["final_equity"] == 500
    assert not result["trades"]
    assert result["first_1000_equity_day"] is None
