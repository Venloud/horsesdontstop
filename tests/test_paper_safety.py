"""Safety contract for the eventual (currently disabled) paper order adapter."""
from datetime import date

import pytest
from bot.paper_safety import (
    PAPER_ENDPOINT, PaperSafetyState, TradeBlocked, execution_blockers,
    make_client_order_id, make_protective_alpaca_order, plan_paper_entry,
)


TODAY = date(2026, 10, 13)


def test_equity_compounds_from_first_profit_before_1000():
    baseline = plan_paper_entry("F", TODAY, 25, 500, 500, .02)
    profit = plan_paper_entry("F", TODAY, 25, 550, 550, .02)
    loss = plan_paper_entry("F", TODAY, 25, 450, 450, .02)
    assert baseline.risk_budget_usd == 10
    assert profit.risk_budget_usd == 11
    assert loss.risk_budget_usd == 9
    assert profit.whole_shares > baseline.whole_shares
    assert loss.whole_shares < baseline.whole_shares
    assert profit.risk_budget_usd > baseline.risk_budget_usd
    assert all(x.estimated_cost <= x.risk_budget_usd * 50 + 1
               for x in (baseline, profit, loss))  # cash must be checked below
    assert baseline.estimated_cost <= 500
    assert profit.estimated_cost <= 550
    assert loss.estimated_cost <= 450
    assert profit.risk_budget_usd < 20  # no wait until $1000


def test_stable_idempotency_key_one_per_symbol_strategy_day():
    a = make_client_order_id(TODAY, "F")
    assert a == make_client_order_id(TODAY, "F")
    assert a != make_client_order_id(date(2026, 10, 14), "F")
    assert a != make_client_order_id(TODAY, "AMD")
    assert len(a) <= 48


def test_whole_share_and_cash_budget_limits():
    trade = plan_paper_entry("F", TODAY, 24.5, 500, 75, .02)
    assert trade.whole_shares <= 3
    assert trade.estimated_cost <= 75
    assert trade.estimated_risk_usd <= trade.risk_budget_usd
    assert trade.protective_stop < trade.estimated_entry


def test_unaffordable_whole_share_rejected():
    with pytest.raises(TradeBlocked, match="No affordable"):
        plan_paper_entry("NVDA", TODAY, 300, 500, 500, .01)
    with pytest.raises(TradeBlocked):
        plan_paper_entry("F", TODAY, 25, 500, 500, .025)
    with pytest.raises(TradeBlocked):
        plan_paper_entry("F", TODAY, float("nan"), 500, 500, .01)
    with pytest.raises(TradeBlocked):
        plan_paper_entry("F", TODAY, 25, 500, 600, .01)


def test_fails_closed_by_default():
    state = PaperSafetyState(
        equity=500, available_cash=500,
        day_open_equity=None, recorded_peak_equity=None,
        open_positions=0, open_orders=0, market_open=True,
        trading_blocked=False, recent_quote_seconds=None
    )
    blocks = execution_blockers(state)
    assert "kill_switch_active" in blocks
    assert "paper_order_submission_disabled" in blocks
    assert "durable_journal_not_ready" in blocks
    assert "broker_orders_not_reconciled" in blocks
    assert "no_healthy_persistent_watchdog" in blocks
    assert "no_fresh_realtime_quote" in blocks
    assert "missing_daily_open_equity" in blocks
    assert "missing_durable_equity_peak" in blocks


def healthy_state(**overrides):
    vals = dict(equity=500, available_cash=500, day_open_equity=500,
                recorded_peak_equity=500, open_positions=0, open_orders=0,
                market_open=True, trading_blocked=False,
                recent_quote_seconds=1, durable_journal_healthy=True,
                reconciled_with_broker=True, watchdog_heartbeat_seconds=1,
                kill_switch_active=False, order_submissions_explicitly_enabled=True,
                broker_endpoint=PAPER_ENDPOINT)
    vals.update(overrides)
    return PaperSafetyState(**vals)


def test_risk_cutoffs_and_existing_orders():
    assert execution_blockers(healthy_state()) == []
    assert "daily_three_percent_loss_cutoff" in execution_blockers(
        healthy_state(equity=485, available_cash=485))
    assert "fifteen_percent_drawdown_cutoff" in execution_blockers(
        healthy_state(equity=425, available_cash=425))
    assert "existing_position_or_open_order" in execution_blockers(
        healthy_state(open_orders=1))
    assert "existing_position_or_open_order" in execution_blockers(
        healthy_state(open_positions=1))
    assert "no_healthy_persistent_watchdog" in execution_blockers(
        healthy_state(watchdog_heartbeat_seconds=121))
    assert "no_fresh_realtime_quote" in execution_blockers(
        healthy_state(recent_quote_seconds=121))
    assert "not_proven_paper_endpoint" in execution_blockers(
        healthy_state(broker_endpoint="https://api.alpaca.markets"))


def test_legacy_market_oto_is_retired_to_prevent_unrestricted_buying():
    plan = plan_paper_entry("F", TODAY, 25, 500, 500, .01)
    with pytest.raises(TradeBlocked, match="retired"):
        make_protective_alpaca_order(plan)
