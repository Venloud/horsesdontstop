"""Lifetime accounting with fills (real 2026-11-10 activity shape)."""
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.paper_accounting import reconcile_cash_lifetime
from bot.paper_safety import TradeBlocked

ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
HASH = hashlib.sha256(ACCOUNT_ID.encode()).hexdigest()
NOW = datetime.now(timezone.utc)
D = lambda y, m, d: int(datetime(y, m, d, 4, tzinfo=timezone.utc).timestamp())

FUND = {"id": "a1", "activity_type": "JNLC", "created_at": "2026-10-11T01:47:47Z",
        "net_amount": "500", "status": "executed", "currency": "USD"}
BUY = {"id": "a2", "activity_type": "FILL", "transaction_time": "2026-11-10T16:10:38Z",
       "type": "fill", "price": "125.41", "qty": "1", "side": "buy", "symbol": "WMT"}
SELL = {"id": "a3", "activity_type": "FILL", "transaction_time": "2026-11-10T16:26:08Z",
        "type": "fill", "price": "125.55", "qty": "1", "side": "sell", "symbol": "WMT"}


def history(today_equity=500.14, today_pnl=0.14):
    return {"timestamp": [D(2026, 10, 12), D(2026, 11, 9), D(2026, 11, 10)],
            "equity": [500.0, 500.0, today_equity], "profit_loss": [0, 0, today_pnl],
            "cashflow": {"JNLC": [500, 0, 0]}}


def account(equity="500.14"):
    return SimpleNamespace(id=ACCOUNT_ID, created_at="2026-10-11T01:40:00Z", currency="USD",
                           equity=equity, cash=equity)


def run(activities, hist=None, acct=None, positions=()):
    return reconcile_cash_lifetime(
        account=acct or account(), activities=activities, history=hist or history(),
        positions=list(positions), orders=[], expected_account_hash=HASH,
        observed_at=NOW, activities_complete=True)


def test_real_round_trip_reconciles_to_the_cent():
    r = run([FUND, BUY, SELL])
    assert r["history_scope"] == "authenticated account lifetime with trading ledger"
    assert (r["realized_pnl"], r["net_external_funding"], r["equity"]) == ("0.14", "500", "500.14")
    assert r["trading_drawdown_limit_breached"] is False and r["equity_history_explained"] is True


def test_cash_only_account_keeps_the_original_scope():
    hist = {"timestamp": [D(2026, 10, 12)], "equity": [500.0], "profit_loss": [0],
            "cashflow": {"JNLC": [500]}}
    r = run([FUND], hist=hist, acct=account("500"))
    assert r["history_scope"] == "authenticated cash-only account lifetime"


def test_cash_mismatch_after_fills_is_refused():
    with pytest.raises(TradeBlocked, match="does not reconcile"):
        run([FUND, BUY, SELL], acct=account("501.00"))


def test_open_shares_or_shorts_are_refused():
    with pytest.raises(TradeBlocked, match="flat"):
        run([FUND, BUY], acct=account("374.59"))
    short = dict(SELL, id="a4")
    with pytest.raises(TradeBlocked, match="short"):
        run([FUND, short])


def test_unknown_activity_types_still_need_manual_review():
    weird = {"id": "a9", "activity_type": "SPIN", "created_at": "2026-11-10T17:00:00Z",
             "net_amount": "5", "status": "executed", "currency": "USD"}
    with pytest.raises(TradeBlocked, match="manual accounting review"):
        run([FUND, BUY, SELL, weird], acct=account("505.14"))


def test_fees_reduce_cash_and_must_be_negative():
    fee = {"id": "a5", "activity_type": "FEE", "created_at": "2026-11-10T16:27:00Z",
           "net_amount": "-0.01", "status": "executed", "currency": "USD"}
    r = run([FUND, BUY, SELL, fee], hist=history(500.13, 0.13), acct=account("500.13"))
    assert r["realized_pnl"] == "0.13"
    with pytest.raises(TradeBlocked, match="fee"):
        run([FUND, BUY, SELL, dict(fee, net_amount="0.01")], acct=account("500.15"))


def test_inconsistent_broker_series_on_trading_days_is_refused():
    with pytest.raises(TradeBlocked, match="internally inconsistent"):
        run([FUND, BUY, SELL], hist=history(today_equity=520.0, today_pnl=0.14))


def test_pre_trade_days_stay_exact():
    hist = history()
    hist["equity"][1] = 499.0   # unexplained loss before any fill
    hist["profit_loss"][1] = -1
    with pytest.raises(TradeBlocked, match="unexplained"):
        run([FUND, BUY, SELL], hist=hist)


def test_malformed_fill_is_refused():
    with pytest.raises(TradeBlocked, match="fill unverified"):
        run([FUND, dict(BUY, qty="0"), SELL])


@pytest.mark.parametrize("change,match", [
    (lambda kw: kw.update(activities_complete=False), "completeness"),
    (lambda kw: kw.update(activities=[]), "evidence missing"),
    (lambda kw: kw.update(history={}), "history incomplete"),
    (lambda kw: kw.update(history={**history(), "cashflow": {"JNLC": [0, 0, 0]}}), "cashflow disagree"),
    (lambda kw: kw.update(activities=[FUND, BUY, dict(SELL, transaction_time="2026-11-10T16:00:00Z")]), "chronology"),
    (lambda kw: kw.update(activities=[FUND, SELL, BUY]), "short"),
    (lambda kw: kw.update(activities=[FUND, BUY, dict(BUY)]), "unverified"),
    (lambda kw: kw.update(expected_account_hash="0" * 64), "identity"),
    (lambda kw: kw.update(positions=[object()]), "exposure"),
])
def test_missing_or_incomplete_evidence_still_blocks(change, match):
    kw = dict(account=account(), activities=[FUND, BUY, SELL], history=history(),
              positions=[], orders=[], expected_account_hash=HASH,
              observed_at=NOW, activities_complete=True)
    change(kw)
    with pytest.raises(TradeBlocked, match=match):
        reconcile_cash_lifetime(**kw)
