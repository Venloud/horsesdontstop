from bot.backtest_cli import summarize
from bot.simulator import Bar, simulate


def test_report_includes_benchmark_and_risk():
    bars = [Bar(100, 101, 99, 100), Bar(100, 103, 99, 102),
            Bar(100, 108, 99, 106)]
    result = summarize("TEST", 0.01, simulate(bars, slippage_bps=0), 100, 106)
    assert result["buy_hold_return_pct"] == 6
    assert result["risk_pct"] == 1
    assert result["trades"] == 1
