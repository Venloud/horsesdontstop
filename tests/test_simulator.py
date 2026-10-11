import pytest
from bot.simulator import Bar, simulate

def test_no_future_lookahead_or_broker():
    bars = [Bar(100, 101, 99, 100), Bar(100, 103, 99, 102),
            Bar(100, 108, 99, 106)]
    result = simulate(bars, slippage_bps=0)
    assert len(result["trades"]) == 1
    assert result["trades"][0].entry_index == 2
    assert result["trades"][0].reason == "target"
    assert result["final_equity"] > 500

def test_gap_stop_can_exceed_planned_loss():
    bars = [Bar(100, 100, 99, 100), Bar(100, 102, 99, 101),
            Bar(100, 101, 98, 100), Bar(90, 92, 89, 91)]
    result = simulate(bars, slippage_bps=0)
    assert result["trades"][0].reason == "gap_stop"
    assert result["trades"][0].pnl < 0

def test_no_trades_without_signal():
    assert simulate([Bar(100, 101, 99, 100)] * 5)["trades"] == []

def test_bad_data():
    with pytest.raises(ValueError):
        simulate([Bar(100, 90, 99, 100)])
