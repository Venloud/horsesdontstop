"""Six-core adapter mechanics, synthetic bars only, no broker API."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.phase2_research_signals import SECTOR_ETF_UNIVERSE, TotalReturnPoint
from bot.phase2_simulator_adapters import (
    SIX_CORE_VARIANTS, adapter_variant_count, next_open_eligibility,
)
from bot.phase2_synthetic_portfolio import run_synthetic_core_portfolio
from bot.strategies import DailyBar


START = date(2025, 1, 1)


def bars(*, slope=.25, count=260, volume_surge=False, start=START):
    result = []
    for i in range(count):
        close = 25.0 + slope*i
        vol = 4000 if volume_surge and (i%30 == 29 or i == count-1) else 1000
        result.append(DailyBar(
            start+timedelta(days=i), close, close+.20,
            close-.20, close, vol))
    return result


def evaluate(strategy, variant, rows=None, **kwargs):
    rows = rows if rows is not None else bars(volume_surge=True)
    return next_open_eligibility(
        strategy, variant, symbol="F",
        completed_history=rows,
        entry_session=rows[-1].day + timedelta(days=1),
        pit_membership_verified=True, **kwargs)


def test_frozen_grid_has_exactly_six_core_and_nineteen_executable_variants():
    assert len(SIX_CORE_VARIANTS) == 6
    assert sum(len(v) for v in SIX_CORE_VARIANTS.values()) == 19
    assert adapter_variant_count() == 21
    assert len(SECTOR_ETF_UNIVERSE) == 12
    assert "XLC" in SECTOR_ETF_UNIVERSE
    assert "XLRE" in SECTOR_ETF_UNIVERSE


@pytest.mark.parametrize("strategy,variant", [
    ("momentum_volume", (21, 1.15)),
    ("momentum_volume", (21, 1.50)),
    ("momentum_volume", (42, 1.15)),
    ("momentum_volume", (42, 1.50)),
    ("trend_following", (20,50)),
    ("trend_following", (20,100)),
    ("trend_following", (50,200)),
    ("high_52_week", (.01,)),
    ("high_52_week", (.02,)),
    ("high_52_week", (.05,)),
])
def test_existing_stock_family_frozen_variants_map_prior_session(strategy,variant):
    result = evaluate(strategy,variant)
    assert result is not None
    assert result.strategy == strategy
    assert result.symbol == "F"
    assert result.variant == variant
    assert result.observed_through < result.entry_session
    assert result.broker_order_allowed is False


def test_sector_leadership_both_variants_use_aligned_previous_sessions():
    stock = bars(slope=.6)
    sector = bars(slope=.3)
    spy = bars(slope=.05)
    for variant in ((63,), (126,)):
        result = evaluate(
            "sector_leadership", variant, stock,
            sector_history=sector, spy_history=spy)
        assert result is not None
    with pytest.raises(ValueError, match="Future"):
        next_open_eligibility(
            "trend_following", (20,50), symbol="F",
            entry_session=stock[-1].day, completed_history=stock,
            pit_membership_verified=True)


def test_short_term_reversal_calls_preregistered_rsi_signal():
    values = [100 + i*.25 for i in range(218)]
    values.extend([values[-1]-2, values[-1]-4])
    rows = [DailyBar(START+timedelta(days=i), c, c+.3, c-.3, c, 1000)
            for i,c in enumerate(values)]
    for variant in ((5,1),(5,3),(10,1),(10,3)):
        result = evaluate("short_term_reversal", variant, rows)
        assert result is not None
        assert result.max_hold_sessions == variant[1]


def test_monthly_etf_signal_enforces_all_twelve_total_return_histories():
    history = [START+timedelta(days=i) for i in range(252)]
    # 2025-09-08 is followed by 2025-09-09 in synthetic calendar.
    # Create an explicit completed month-end / next month open pair.
    history += [date(2025,9,30), date(2025,10,1)]
    selected = {}
    for sym in SECTOR_ETF_UNIVERSE:
        gain = .003 if sym=="XLK" else .001
        selected[sym] = [
            TotalReturnPoint(day,100*(1+gain*i))
            for i, day in enumerate(history)
        ]
    for lookback in (63,126,252):
        decision = next_open_eligibility(
            "etf_dual_momentum", (lookback,),
            entry_session=date(2025,10,1),
            total_return_series=selected,
            verified_session_calendar=history,
            total_return_verified=True,
            calendar_verified=True,
            pit_membership_verified=True)
        assert decision is not None
        assert decision.symbol == "XLK"
        assert decision.observed_through == date(2025,9,30)
    assert next_open_eligibility(
        "etf_dual_momentum", (63,),
        entry_session=date(2025,10,1),
        total_return_series=selected,
        verified_session_calendar=history,
        total_return_verified=False,
        calendar_verified=True,
        pit_membership_verified=True) is None


def test_unapproved_parameter_and_overnight_execution_refused():
    for name, variant in (
        ("momentum_volume", (22,1.15)),
        ("high_52_week", (.10,)),
        ("etf_dual_momentum", (30,)),
        ("unknown_strategy", (63,)),
        ("overnight_drift", (False,)),
    ):
        with pytest.raises(ValueError):
            evaluate(name, variant)
    assert next_open_eligibility(
        "trend_following", (20,50), symbol="F",
        entry_session=START+timedelta(days=260),
        completed_history=bars(), pit_membership_verified=False) is None


def test_synthetic_cash_portfolio_has_one_position_and_1pct_risk():
    days = bars(count=130, volume_surge=True)
    result = run_synthetic_core_portfolio(
        {"SPY": bars(count=130, slope=.01), "F": days},
        "momentum_volume", (21,1.15), ["F"],
        synthetic_fixture=True, pit_membership_verified=True,
        calendar_verified=True)
    assert result["simulator_mode"] == "synthetic_mechanics_only_not_phase3"
    assert result["risk_fraction_max"] == .01
    assert result["slippage_bps_per_side"] == 30
    assert result["broker_order_allowed"] is False
    assert result["final_equity"] > 0
    assert result["trades"]
    assert all(isinstance(t["shares"], int) and t["shares"] > 0
               for t in result["trades"])
    assert all(a["sell_day"] < b["buy_day"]
               for a,b in zip(result["trades"], result["trades"][1:]))
    assert all(t["buy_fill"] > 0 and t["sell_fill"] > 0
               for t in result["trades"])


def test_synthetic_simulator_is_locked_out_of_real_scored_research():
    panel = {"SPY": bars(count=130, slope=.01), "F": bars(count=130)}
    with pytest.raises(ValueError, match="Synthetic adapter"):
        run_synthetic_core_portfolio(
            panel, "trend_following", (20,50), ["F"],
            pit_membership_verified=True,calendar_verified=True)


def test_synthetic_portfolio_does_not_connect_to_alpaca_or_neon():
    root = Path(__file__).resolve().parents[1]
    for f in ("phase2_simulator_adapters.py", "phase2_synthetic_portfolio.py"):
        source = (root/"bot"/f).read_text()
        for prohibited in ("submit_order(", "TradingClient(", "psycopg",
                           "neon.connect", "cancel_orders(", "alpaca.trading"):
            assert prohibited not in source
