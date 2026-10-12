"""Offline synthetic-only checks for the three Phase 2 research hypotheses."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.strategies import DailyBar
from bot.phase2_research_signals import (
    SECTOR_ETF_UNIVERSE, TotalReturnPoint,
    short_term_reversal_signal, etf_dual_momentum_signal,
    overnight_drift_research_signal,
)


START = date(2024, 1, 1)
MONTH_END = date(2026, 9, 30)
NEXT_MONTH = date(2026, 10, 1)


def bars(closes, *, start=START):
    return [
        DailyBar(start + timedelta(days=i), c, c + 0.5, c - 0.5, c, 1000)
        for i, c in enumerate(closes)
    ]


def rising_then_down():
    values = [100 + 0.25 * i for i in range(218)]
    return bars(values + [values[-1] - 2, values[-1] - 4])


def daily_index(*, xlk_gain=0.001, default_gain=0.0002):
    result = {}
    for ticker in SECTOR_ETF_UNIVERSE:
        gain = xlk_gain if ticker == "XLK" else (
            0.0001 if ticker == "SPY" else default_gain)
        result[ticker] = [
            TotalReturnPoint(MONTH_END - timedelta(days=252 - i),
                             100.0 * (1 + gain * i))
            for i in range(253)
        ]
    return result


def ranked(data, **overrides):
    args = dict(
        completed_session=MONTH_END, next_session=NEXT_MONTH,
        lookback_sessions=63, total_return_source_verified=True,
        point_in_time_universe_verified=True,
        next_session_calendar_verified=True,
        verified_session_calendar=[MONTH_END, NEXT_MONTH],
    )
    args.update(overrides)
    return etf_dual_momentum_signal(data, **args)


@pytest.mark.parametrize("threshold", (5, 10))
@pytest.mark.parametrize("hold", (1, 3))
def test_reversal_four_preregistered_variants_use_closed_down_bar(threshold, hold):
    history = rising_then_down()
    decision = short_term_reversal_signal(
        history, entry_session=history[-1].day + timedelta(days=1),
        rsi_threshold=threshold, hold_sessions=hold)
    assert decision is not None
    assert decision.rsi2 <= threshold
    assert decision.sma200 > decision.previous_sma200
    assert decision.hold_sessions == hold
    assert decision.broker_order_allowed is False


def test_reversal_rejects_early_history_and_up_closes():
    early = bars([100 + i * .2 for i in range(199)])
    assert short_term_reversal_signal(
        early, entry_session=early[-1].day + timedelta(days=1),
        rsi_threshold=5, hold_sessions=1) is None
    trending = bars([100 + i * .2 for i in range(220)])
    assert short_term_reversal_signal(
        trending, entry_session=trending[-1].day + timedelta(days=1),
        rsi_threshold=10, hold_sessions=3) is None


def test_reversal_rejects_same_day_or_future_inputs_and_unapproved_parameters():
    history = rising_then_down()
    with pytest.raises(ValueError, match="Future or entry-session"):
        short_term_reversal_signal(
            history, entry_session=history[-1].day,
            rsi_threshold=5, hold_sessions=1)
    for threshold, hold in ((15, 1), (5, 2), (True, 1)):
        with pytest.raises(ValueError, match="Non-preregistered"):
            short_term_reversal_signal(
                history, entry_session=history[-1].day + timedelta(days=1),
                rsi_threshold=threshold, hold_sessions=hold)


@pytest.mark.parametrize("lookback", (63, 126, 252))
def test_etf_dual_momentum_three_approved_lookbacks_rank_total_return(lookback):
    decision = ranked(daily_index(), lookback_sessions=lookback)
    assert decision is not None
    assert decision.selected_symbol == "XLK"
    assert decision.lookback_sessions == lookback
    assert decision.next_rebalance_session == NEXT_MONTH
    assert decision.qualifying_return > 0
    assert decision.broker_order_allowed is False


def test_etf_dual_momentum_holds_cash_when_no_asset_has_positive_return():
    data = daily_index()
    for ticker in data:
        data[ticker] = [
            TotalReturnPoint(x.day, 100 - 0.01 * i)
            for i, x in enumerate(data[ticker])
        ]
    result = ranked(data)
    assert result is not None
    assert result.selected_symbol is None
    assert result.qualifying_return < 0


def test_etf_dual_momentum_fails_closed_on_missing_data_or_source_proof():
    data = daily_index()
    assert ranked(data, total_return_source_verified=False) is None
    assert ranked(data, point_in_time_universe_verified=False) is None
    assert ranked(data, next_session_calendar_verified=False) is None
    with pytest.raises(ValueError, match="follow completed"):
        ranked(data, next_session=MONTH_END)
    with pytest.raises(ValueError, match="follow completed"):
        ranked(data, next_session=date(2026, 9, 29))
    assert ranked({k: v for k, v in data.items() if k != "XLE"}) is None
    misaligned = dict(data)
    misaligned["XLK"] = data["XLK"][:-1]
    assert ranked(misaligned) is None
    with pytest.raises(ValueError, match="Non-preregistered"):
        ranked(data, lookback_sessions=30)


def test_etf_monthly_only_no_same_month_rebalance():
    decision = ranked(daily_index(), next_session=date(2026, 10, 1))
    assert decision is not None
    assert ranked(daily_index(), next_session=date(2026, 10, 1)) is not None


def test_overnight_research_never_claims_valid_close_auction_execution():
    rising = bars([100 + i * 0.5 for i in range(24)])
    entry = rising[-1].day + timedelta(days=1)
    no_filter = overnight_drift_research_signal(
        rising, proposed_entry_close_session=entry,
        require_positive_spy_20=False)
    filtered = overnight_drift_research_signal(
        rising, proposed_entry_close_session=entry,
        require_positive_spy_20=True)
    for decision in (no_filter, filtered):
        assert decision.hypothesis_eligible
        assert decision.exit_next_open
        assert decision.broker_order_allowed is False
        assert decision.auction_execution_verified is False


def test_overnight_filter_handles_downtrend_without_current_session_close():
    falling = bars([110 - i * 0.5 for i in range(24)])
    entry = falling[-1].day + timedelta(days=1)
    yes = overnight_drift_research_signal(
        falling, proposed_entry_close_session=entry,
        require_positive_spy_20=True)
    no = overnight_drift_research_signal(
        falling, proposed_entry_close_session=entry,
        require_positive_spy_20=False)
    assert yes.hypothesis_eligible is False
    assert no.hypothesis_eligible is True
    with pytest.raises(ValueError, match="Future or entry-session"):
        overnight_drift_research_signal(
            falling, proposed_entry_close_session=falling[-1].day,
            require_positive_spy_20=False)


def test_research_signal_module_has_no_network_broker_or_trade_boundary():
    source = (
        Path(__file__).resolve().parents[1] /
        "bot" / "phase2_research_signals.py"
    ).read_text()
    for forbidden in (
        "TradingClient", "submit_order(", "cancel_order(",
        "alpaca.trading", "psycopg", "neon.connect", "requests.get(",
    ):
        assert forbidden not in source


def test_etf_monthly_needs_explicit_consecutive_exchange_sessions():
    data = daily_index()
    assert ranked(data, verified_session_calendar=None) is None
    assert ranked(data, verified_session_calendar=[]) is None
    assert ranked(data, verified_session_calendar=[MONTH_END]) is None
    assert ranked(data, verified_session_calendar=[NEXT_MONTH, MONTH_END]) is None
    assert ranked(data, verified_session_calendar=[MONTH_END, NEXT_MONTH]) is not None


def test_etf_monthly_rejects_skipping_actual_next_session():
    data = daily_index()
    dates = [MONTH_END, NEXT_MONTH, date(2026, 10, 2)]
    assert ranked(data, next_session=date(2026, 10, 2),
                  verified_session_calendar=dates) is None
    assert ranked(data, verified_session_calendar=dates) is not None
    assert ranked(data, verified_session_calendar=[
        MONTH_END, NEXT_MONTH, NEXT_MONTH]) is None
