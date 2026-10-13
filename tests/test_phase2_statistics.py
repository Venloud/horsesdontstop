"""Synthetic-only regression tests for preregistered Phase 2 statistics."""
from datetime import date, timedelta
from math import isfinite
from pathlib import Path

import pytest

from bot.phase2_statistics import (
    Contribution, DatedTradeReturn, MatchedExit,
    archived_legacy_trial_count, date_block_bootstrap,
    deflated_sharpe_nominal, matched_deposit_spy_benchmark,
    matched_horizon_placebo,
)


START = date(2026, 1, 1)


def test_date_block_bootstrap_clusters_same_date_instruments():
    # Every date has +1R and -1R in two instruments. Sampling individual
    # trades separately would break the contemporaneous dependence.
    trades = [
        DatedTradeReturn(START + timedelta(days=i), symbol, score)
        for i in range(60)
        for symbol, score in [("AAA", 1.), ("BBB", -1.)]
    ]
    result = date_block_bootstrap(
        trades, block_sessions=5, seed=191, replicates=10000)
    assert result.mean_net_r == 0
    assert result.lower_95_r == 0
    assert result.upper_95_r == 0
    assert result.trading_sessions == 60
    assert result.block_sessions == 5


def test_date_block_bootstrap_recovers_planted_fixed_positive_r():
    trades = [DatedTradeReturn(START + timedelta(days=i), "AAA", .2)
              for i in range(70)]
    result = date_block_bootstrap(
        trades, block_sessions=7, seed=12, replicates=10000)
    assert result.lower_95_r == pytest.approx(.2, abs=1e-12)
    assert result.upper_95_r == pytest.approx(.2, abs=1e-12)
    assert result.replicates == 10000


@pytest.mark.parametrize("size,seed,replicates", [
    (0, 2, 10000), (35, -1, 10000), (35, 2, 9999),
    (35, True, 10000), (35, 2, True),
])
def test_unfrozen_or_insufficient_bootstrap_fails(size, seed, replicates):
    trades = [DatedTradeReturn(START + timedelta(days=i), "AAA", .1)
              for i in range(60)]
    with pytest.raises(ValueError):
        date_block_bootstrap(
            trades, block_sessions=size, seed=seed, replicates=replicates)


def test_random_placebo_preserves_symbol_shares_horizon_and_costs():
    opens = {"AAA": [100.0]*110, "BBB": [80.0]*110}
    actual = [MatchedExit("AAA", 5, 9, 3),
              MatchedExit("BBB", 35, 38, 2)]
    # Candidate entry positions can move but each trade's exit stays
    # exactly its original holding horizon, not a profitable new exit.
    eligible = {"AAA": list(range(1, 100)),
                "BBB": list(range(1, 100))}
    result = matched_horizon_placebo(
        opens, actual, eligible_entries=eligible, seed=83, trials=10000)
    exact_flat_bps = ((1-.003)/(1+.003)-1)*10000
    assert result.strategy_mean_net_bps == pytest.approx(exact_flat_bps)
    assert result.placebo_95th_percentile_bps == pytest.approx(exact_flat_bps)
    assert result.sessions_exposed == 7
    assert result.total_share_sessions == 18
    assert result.stop_policy_verified is False


def test_placebo_cannot_change_frozen_cost_or_complete_overlap_constraints():
    actual = [MatchedExit("AAA", 2, 4, 1), MatchedExit("AAA", 5, 7, 1)]
    opens = {"AAA": [100.]*20}
    with pytest.raises(ValueError, match="overlapping placebo"):
        matched_horizon_placebo(
            opens, actual,
            eligible_entries={"AAA": [2]}, seed=1, trials=10000)
    with pytest.raises(ValueError, match="Unpreregistered"):
        matched_horizon_placebo(
            opens, actual,
            eligible_entries={"AAA": [2, 10]},
            slippage_bps_per_side=10, seed=1, trials=10000)


def test_dsr_counts_original_80_attempts_but_never_certifies_promotion():
    ledger = Path(__file__).resolve().parents[1] / "research" / "TRIAL_LEDGER.csv"
    prior = archived_legacy_trial_count(ledger)
    assert prior == 80
    returns = [0.002 + .014*((i%9)-4)/4 for i in range(240)]
    nominal = deflated_sharpe_nominal(
        returns, logged_trial_count=prior+21)
    assert nominal.raw_trial_count == 101
    assert 0 <= nominal.nominal_probability <= 1
    assert isfinite(nominal.comparison_threshold)
    assert nominal.promotion_valid is False
    more_tests = deflated_sharpe_nominal(
        returns, logged_trial_count=prior+40)
    assert more_tests.nominal_probability <= nominal.nominal_probability


def test_dsr_blocks_invalid_missing_trial_history_and_zero_variance():
    returns = [0.01] * 50
    with pytest.raises(ValueError):
        deflated_sharpe_nominal(returns, logged_trial_count=80)
    with pytest.raises(ValueError):
        deflated_sharpe_nominal(
            [.001, -.001] * 60, logged_trial_count=21)


def test_same_actual_deposit_dates_shift_identically_for_both_portfolios():
    sessions = [date(2026, 1, 1), date(2026, 1, 2),
                date(2026, 1, 5), date(2027, 1, 1)]
    spy = [(d, 100.0) for d in sessions]
    actual_deposits = [Contribution(date(2026, 1, 3), 100.)]
    active = [(sessions[0],500.), (sessions[1],500.),
              (sessions[2],600.), (sessions[3],600.)]
    result = matched_deposit_spy_benchmark(
        spy, active, actual_deposits,
        active_contributions=actual_deposits)
    assert result.applied_contributions == (
        Contribution(date(2026, 1, 5), 100.),)
    assert result.active_time_weighted_return == pytest.approx(0)
    assert result.active_money_weighted_annualized == pytest.approx(0, abs=1e-9)
    assert result.passive_time_weighted_return < 0
    assert result.passive_money_weighted_annualized < 0
    assert result.passive_fractional_index_units is True
    assert result.passive_equity[-1] < 600


def test_passive_total_return_index_respects_all_dividend_growth():
    sessions = [date(2026, 1, 1), date(2027, 1, 1)]
    result = matched_deposit_spy_benchmark(
        [(sessions[0],100), (sessions[1],120)],
        [(sessions[0],500), (sessions[1],600)],
        [], active_contributions=[])
    analytical = (120*.997/(100*1.003))-1
    assert result.passive_time_weighted_return == pytest.approx(analytical)
    assert result.active_time_weighted_return == pytest.approx(.2)
    assert result.applied_contributions == ()


def test_benchmark_rejects_invented_or_unmatched_owner_deposits():
    sessions = [date(2026, 1, 1), date(2027, 1, 1)]
    prices = [(sessions[0],100), (sessions[1],110)]
    nav = [(sessions[0],500), (sessions[1],550)]
    with pytest.raises(ValueError, match="schedule mismatch"):
        matched_deposit_spy_benchmark(
            prices, nav, [Contribution(date(2026, 6, 1),100)],
            active_contributions=[])
    with pytest.raises(ValueError, match="out-of-window"):
        matched_deposit_spy_benchmark(
            prices, nav, [Contribution(date(2027, 1, 3),100)],
            active_contributions=[Contribution(date(2027, 1, 3),100)])


def test_statistics_module_does_not_make_network_or_broker_calls():
    content = (
        Path(__file__).resolve().parents[1] /
        "bot" / "phase2_statistics.py"
    ).read_text()
    for forbidden in ("TradingClient", "submit_order(", "requests.",
                      "psycopg", "neon.connect", "alpaca.", "UPDATE public."):
        assert forbidden not in content
