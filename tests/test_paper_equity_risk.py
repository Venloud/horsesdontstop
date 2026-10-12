"""Synthetic-only daily/peak PAPER equity risk evidence checks.

No broker requests, Neon queries, order submissions, deposits or schema writes.
These tests verify that real external deposits/withdrawals cannot masquerade
as returns and a missing/stale/unverified history NEVER authorizes risk.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot.paper_equity_risk import (
    AuditedExternalCashFlow, ObservedPaperEquity,
    contribution_adjusted_risk,
)
from bot.paper_safety import TradeBlocked

UTC = timezone.utc
FRIDAY = datetime(2026, 10, 9, 13, 31, tzinfo=UTC)
OPEN = datetime(2026, 10, 12, 13, 31, tzinfo=UTC)
DEPOSIT = datetime(2026, 10, 12, 13, 40, tzinfo=UTC)
CURRENT = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)


def equity(time, dollars, cash=None):
    return ObservedPaperEquity(
        observed_at=time,
        equity_usd=Decimal(str(dollars)),
        cash_usd=Decimal(str(dollars if cash is None else cash)),
    )


def flow(amount, when=DEPOSIT, *, event_id="broker-verified-001", verified=True):
    return AuditedExternalCashFlow(
        event_id=event_id, occurred_at=when,
        amount_usd=Decimal(str(amount)), verified_by_broker=verified,
    )


def calculate(samples, flows=(), *, now=CURRENT, **flags):
    proofs = dict(
        all_equity_samples_accounted_for=True,
        all_external_flows_reconciled=True,
        exchange_calendar_verified=True,
    )
    proofs.update(flags)
    return contribution_adjusted_risk(
        list(samples), list(flows), as_of_utc=now, **proofs
    )


def test_flat_account_with_no_cash_flow_has_no_loss():
    report = calculate([equity(FRIDAY, 500),
                        equity(OPEN, 500), equity(CURRENT, 500)])
    assert report.opening_equity_usd == 500
    assert report.current_performance_equity_usd == 500
    assert report.verified_peak_performance_equity_usd == 500
    assert report.loss_from_open_pct == 0
    assert report.drawdown_from_peak_pct == 0
    assert report.safe_under_numeric_limits
    assert not report.daily_loss_limit_breached


def test_cash_deposit_cannot_hide_three_percent_trading_loss():
    # $100 deposit raises raw equity from $485 to $585, but this
    # still represents a $15 loss (3% of the $500 opening baseline).
    report = calculate([
        equity(FRIDAY, 500),
        equity(OPEN, 500),
        equity(CURRENT, 585),
    ], [flow(100)])
    assert report.current_performance_equity_usd == 485
    assert report.external_flows_since_open_usd == 100
    assert report.loss_from_open_pct == 3
    assert report.daily_loss_limit_breached
    assert not report.safe_under_numeric_limits


def test_deposit_alone_does_not_count_as_strategy_profit():
    report = calculate([
        equity(FRIDAY, 500), equity(OPEN, 500),
        equity(CURRENT, 600),
    ], [flow(100)])
    assert report.current_performance_equity_usd == 500
    assert report.verified_peak_performance_equity_usd == 500
    assert report.loss_from_open_pct == 0
    assert report.drawdown_from_peak_pct == 0
    assert report.safe_under_numeric_limits


def test_withdrawal_alone_does_not_create_false_loss():
    report = calculate([
        equity(FRIDAY, 500), equity(OPEN, 500),
        equity(CURRENT, 400),
    ], [flow(-100)])
    assert report.current_performance_equity_usd == 500
    assert report.loss_from_open_pct == 0
    assert report.drawdown_from_peak_pct == 0
    assert report.safe_under_numeric_limits


def test_exact_fifteen_percent_peak_drawdown_blocks():
    # $600 previous high -> $510 current is exactly -15%;
    # $525 today's opening -> $510 is <3%, so ONLY peak blocks.
    report = calculate([
        equity(FRIDAY, 600), equity(OPEN, 525),
        equity(CURRENT, 510),
    ])
    assert report.verified_peak_performance_equity_usd == 600
    assert report.drawdown_from_peak_pct == 15
    assert report.peak_drawdown_limit_breached
    assert not report.daily_loss_limit_breached
    assert not report.safe_under_numeric_limits


def test_exact_three_percent_daily_loss_blocks():
    report = calculate([
        equity(FRIDAY, 500), equity(OPEN, 500),
        equity(CURRENT, 485),
    ])
    assert report.loss_from_open_pct == 3
    assert report.daily_loss_limit_breached


def test_just_below_thresholds_do_not_trigger_numeric_limit():
    report = calculate([
        equity(FRIDAY, 500), equity(OPEN, 500),
        equity(CURRENT, 485.01),
    ])
    assert not report.daily_loss_limit_breached
    assert report.safe_under_numeric_limits


@pytest.mark.parametrize("bad_proof", [
    "all_equity_samples_accounted_for",
    "all_external_flows_reconciled",
    "exchange_calendar_verified",
])
def test_missing_independent_proof_always_blocks(bad_proof):
    with pytest.raises(TradeBlocked, match="Cannot verify"):
        calculate([
            equity(FRIDAY, 500), equity(OPEN, 500),
            equity(CURRENT, 500),
        ], **{bad_proof: False})


def test_missing_today_open_or_duplicate_baseline_aborts():
    for samples in (
        [equity(FRIDAY, 500), equity(CURRENT, 500)],
        [equity(FRIDAY, 500), equity(OPEN, 500),
         equity(OPEN + timedelta(seconds=10), 500),
         equity(CURRENT, 500)],
    ):
        with pytest.raises(TradeBlocked, match="opening equity"):
            calculate(samples)


def test_future_stale_duplicate_or_unsorted_prices_block():
    cases = [
        ([equity(FRIDAY, 500), equity(OPEN, 500),
          equity(CURRENT - timedelta(seconds=121), 500)], CURRENT),
        ([equity(FRIDAY, 500), equity(OPEN, 500),
          equity(CURRENT + timedelta(seconds=1), 500)], CURRENT),
        ([equity(FRIDAY, 500), equity(CURRENT, 500),
          equity(OPEN, 500)], CURRENT),
        ([equity(FRIDAY, 500), equity(OPEN, 500),
          equity(CURRENT, 500), equity(CURRENT, 500)], CURRENT),
    ]
    for samples, asof in cases:
        with pytest.raises(TradeBlocked):
            calculate(samples, now=asof)


@pytest.mark.parametrize("bad_flow", [
    flow(100, verified=False),
    flow(0),
    flow(100, event_id=""),
    flow(100, when=FRIDAY),
    flow(100, when=CURRENT + timedelta(seconds=1)),
    flow(100, when=CURRENT + timedelta(seconds=30)),
    flow(100, when=OPEN),
])
def test_unverified_zero_dated_wrong_or_ambiguous_cash_flow_fails_closed(bad_flow):
    with pytest.raises(TradeBlocked):
        calculate([
            equity(FRIDAY, 500), equity(OPEN, 500),
            equity(CURRENT, 600),
        ], [bad_flow])


def test_duplicate_cash_flow_id_fails_closed():
    with pytest.raises(TradeBlocked, match="duplicated"):
        calculate([
            equity(FRIDAY, 500), equity(OPEN, 500),
            equity(CURRENT, 700),
        ], [flow(100), flow(100)])


def test_flow_after_last_snapshot_requires_fresh_broker_equity():
    almost_now = CURRENT - timedelta(seconds=30)
    with pytest.raises(TradeBlocked, match="after latest"):
        calculate([
            equity(FRIDAY, 500), equity(OPEN, 500),
            equity(almost_now, 500),
        ], [flow(100, when=CURRENT - timedelta(seconds=10))])


def test_naive_timestamp_weekend_and_before_open_are_blocked():
    with pytest.raises(TradeBlocked):
        calculate([
            equity(FRIDAY, 500), equity(OPEN, 500),
            equity(CURRENT, 500),
        ], now=CURRENT.replace(tzinfo=None))
    sunday = datetime(2026, 10, 11, 14, 0, tzinfo=UTC)
    with pytest.raises(TradeBlocked):
        calculate([
            equity(FRIDAY, 500),
            equity(sunday, 500),
        ], now=sunday)
    before_open = datetime(2026, 10, 12, 13, 29, tzinfo=UTC)
    with pytest.raises(TradeBlocked):
        calculate([
            equity(FRIDAY, 500),
            equity(before_open, 500),
        ], now=before_open)


def test_no_broker_neon_or_order_write_paths_in_offline_risk_calculator():
    from pathlib import Path
    import bot.paper_equity_risk as module
    code = Path(module.__file__).read_text()
    for forbidden in (
        "TradingClient(", ".submit_order(", "psycopg.connect(",
        ".cancel_order(", ".close_position(", "INSERT INTO ",
        "UPDATE public.paper_control",
    ):
        assert forbidden not in code
