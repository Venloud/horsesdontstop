"""Live PAPER order preview tests, strictly offline and no trade submission."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

from bot.live_quotes import (
    PILOT_FEE_RESERVE_USD, PILOT_MAX_PURCHASE_USD,
    build_live_protected_oto_request, preview_live_paper_entry,
)
from bot.paper_safety import TradeBlocked


NOW = datetime(2026, 10, 12, 14, 35, 0, tzinfo=timezone.utc)


def quote(*, bid=25, ask=25.02, bsize=20, asize=20, age=2):
    return SimpleNamespace(
        bid_price=bid, ask_price=ask,
        bid_size=bsize, ask_size=asize,
        timestamp=NOW - timedelta(seconds=age),
    )


def plan(q=None, **overrides):
    vals = dict(symbol="F", quote=q or quote(bid=1.50, ask=1.51), equity=500,
                available_cash=500, now=NOW, market_open=True)
    vals.update(overrides)
    return preview_live_paper_entry(**vals)


def test_fresh_quote_creates_unsent_one_percent_protected_limit_oto():
    p = plan()
    assert p.quote_feed == "IEX_ONLY_NOT_NBBO"
    assert p.order_submission_enabled is False
    assert p.shares > 0
    assert p.risk_budget_usd == 5.00
    assert p.stop_distance_risk_usd <= 5.0
    assert p.max_limit_purchase_usd <= PILOT_MAX_PURCHASE_USD
    assert p.fee_reserve_usd == PILOT_FEE_RESERVE_USD
    assert p.order_time_in_force == "gtc"
    assert p.buy_limit_usd > p.ask_usd
    assert p.stop_usd < p.bid_usd
    request = build_live_protected_oto_request(p)
    assert request.order_class == OrderClass.OTO
    assert request.side == OrderSide.BUY
    assert request.time_in_force == TimeInForce.GTC
    assert request.extended_hours is False
    assert request.stop_loss.stop_price == p.stop_usd
    assert request.limit_price == p.buy_limit_usd
    assert request.take_profit is None
    assert request.client_order_id == p.client_order_id


def test_max_one_percent_budget_strict_no_two_percent_default():
    with pytest.raises(TradeBlocked, match="restricted to 1%"):
        plan(risk_fraction=.02)
    smaller = plan(equity=400, available_cash=400)
    larger = plan(equity=600, available_cash=600)
    assert smaller.risk_budget_usd == 4.0
    assert larger.risk_budget_usd == 6.0
    assert smaller.max_limit_purchase_usd <= 4.0
    assert larger.max_limit_purchase_usd <= 5.0


def test_quote_liquidity_constrains_whole_shares():
    p = plan(quote(bid=1.50, ask=1.51, asize=1))
    assert p.shares == 1
    assert p.max_limit_purchase_usd <= PILOT_MAX_PURCHASE_USD


def test_first_pilot_purchase_cap_is_five_dollars_plus_separate_fee_reserve():
    p = plan(equity=500, available_cash=500)
    assert p.max_limit_purchase_usd <= 5.00
    assert p.fee_reserve_usd == .50
    assert p.max_limit_purchase_usd + p.fee_reserve_usd <= 5.50
    with pytest.raises(TradeBlocked, match="No affordable whole"):
        plan(quote=quote(bid=5.00, ask=5.01), equity=500,
             available_cash=500)
    with pytest.raises(TradeBlocked, match="No affordable whole"):
        plan(quote=quote(bid=4.48, ask=4.50), equity=500,
             available_cash=5.49)


def test_market_closed_rejected_even_with_recent_price():
    with pytest.raises(TradeBlocked, match="session"):
        plan(market_open=False)


@pytest.mark.parametrize("age", [46, 150, -5])
def test_stale_or_future_quotes_rejected(age):
    with pytest.raises(TradeBlocked, match="stale"):
        plan(quote(age=age))


@pytest.mark.parametrize("bid,ask", [(25, 24.9), (25, 25),
                                      (25, 27), (0, 25), (25, -1)])
def test_bad_or_wide_quotes_rejected(bid, ask):
    with pytest.raises(TradeBlocked):
        plan(quote(bid=bid, ask=ask))


@pytest.mark.parametrize("bsize,asize", [(0, 10), (10, 0), (-1, 10),
                                         (None, 10), (1.5, 2)])
def test_missing_or_invalid_quote_sizes_block(bsize, asize):
    with pytest.raises(TradeBlocked):
        plan(quote(bsize=bsize, asize=asize))


def test_whole_share_unaffordable_at_risk_budget_blocks():
    with pytest.raises(TradeBlocked, match="No affordable whole"):
        plan(quote(bid=200, ask=200.02), available_cash=500)


def test_reject_invalid_market_clock_and_equity():
    with pytest.raises(TradeBlocked):
        plan(now=datetime(2026, 10, 12, 14, 35))
    with pytest.raises(TradeBlocked):
        plan(equity=0)
    with pytest.raises(TradeBlocked):
        plan(equity=float("nan"))
    with pytest.raises(TradeBlocked):
        plan(available_cash=600)


def test_deterministic_new_york_session_cid():
    x = plan()
    y = plan()
    assert x.client_order_id == y.client_order_id
    z = plan(now=NOW + timedelta(days=1),
             quote=quote(bid=1.50, ask=1.51, age=-86400))
    assert z.client_order_id != x.client_order_id


def test_modified_nonpreview_request_denied():
    from dataclasses import replace
    p = plan()
    with pytest.raises(TradeBlocked):
        build_live_protected_oto_request(
            replace(p, order_submission_enabled=True))
    with pytest.raises(TradeBlocked):
        build_live_protected_oto_request(
            replace(p, order_time_in_force="day"))


@pytest.mark.parametrize("hour,minute", [
    (15, 30), (15, 58), (16, 0), (9, 29),
])
def test_preview_refuses_last_regular_half_hour_or_premarket(hour, minute):
    from zoneinfo import ZoneInfo
    local = datetime(2026, 10, 21, hour, minute,
                     tzinfo=ZoneInfo("America/New_York"))
    at = local.astimezone(timezone.utc)
    q = SimpleNamespace(timestamp=at, bid_price=4.78, ask_price=4.80,
                        bid_size=5, ask_size=5)
    with pytest.raises(TradeBlocked, match="safe regular-session window"):
        preview_live_paper_entry(symbol="DIS", quote=q, equity=500,
                                 available_cash=500, now=at,
                                 market_open=True)


def test_regular_session_preview_before_cutoff_is_still_read_only():
    from zoneinfo import ZoneInfo
    local = datetime(2026, 10, 21, 15, 29,
                     tzinfo=ZoneInfo("America/New_York"))
    at = local.astimezone(timezone.utc)
    q = SimpleNamespace(timestamp=at, bid_price=4.78, ask_price=4.80,
                        bid_size=5, ask_size=5)
    result = preview_live_paper_entry(symbol="DIS", quote=q, equity=500,
                                      available_cash=500, now=at,
                                      market_open=True)
    assert result.order_submission_enabled is False
