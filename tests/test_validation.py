from types import SimpleNamespace
import pytest
from bot.validation import audit_trades, conservative_exit

def test_gap_stop_fills_below_stop():
    price, reason = conservative_exit(90, 89, 92, 97, 106, 0)
    assert (price, reason) == (90, "gap_stop")

def test_stop_precedes_target_in_same_bar():
    price, reason = conservative_exit(100, 96, 108, 97, 106, 0)
    assert (price, reason) == (97, "stop")

def test_closed_trade_drawdown():
    trades = [SimpleNamespace(pnl=x) for x in (20, -30, 10)]
    report = audit_trades(trades)
    assert report.ending_equity == 500
    assert report.max_closed_trade_drawdown_pct == 5.77
    assert report.profit_factor == 1.0

def test_no_trade_is_not_a_win():
    report = audit_trades([])
    assert report.win_rate_pct is None
    assert report.profit_factor is None

def test_invalid_equity():
    with pytest.raises(ValueError):
        audit_trades([], 0)
