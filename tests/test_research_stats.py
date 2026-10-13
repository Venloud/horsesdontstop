"""Section 6 statistics: synthetic/known-answer tests only. No network."""
import json
import math
import random
import zipfile
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist, mean, pstdev, stdev

import pytest

from bot.portfolio_research import run_portfolio
from bot.research_harness import (
    PlantedJump, legacy_rows, marker_score_fn, random_entry_score_fn,
    synthetic_flat_bars, trade_r_multiples,
)
from bot.research_stats import (
    align_cash_flows, date_block_bootstrap, deflated_sharpe,
    expected_max_sharpe, exposure_fraction, ledger_trial_count,
    matched_deposit_benchmark, money_weighted_return, per_trade_sharpes,
    placebo_test, probabilistic_sharpe, time_weighted_return,
)
from bot.strategies import DailyBar

ROOT = Path(__file__).resolve().parents[1]
SYMS = [f"S{i}" for i in range(10)]


# --------------------------------------------------------------- bootstrap

def test_bootstrap_matches_normal_theory_for_iid_daily_values():
    rng = random.Random(1)
    vals = [rng.gauss(0.1, 1) for _ in range(400)]
    b = date_block_bootstrap(list(range(400)), vals, 400, block_length=1,
                             resamples=4000, seed=7)
    se = stdev(vals) / math.sqrt(400)
    assert b.upper - b.lower == pytest.approx(2 * 1.96 * se, rel=0.12)
    assert b.lower < b.mean < b.upper


def test_same_day_clusters_widen_the_interval():
    rng = random.Random(2)
    idx, vals = [], []
    for day in range(300):
        shock = rng.gauss(0, 1)
        for _ in range(5):  # five correlated trades per session
            idx.append(day)
            vals.append(shock + rng.gauss(0, 0.2))
    b = date_block_bootstrap(idx, vals, 300, block_length=5, resamples=3000, seed=3)
    naive = 2 * 1.96 * stdev(vals) / math.sqrt(len(vals))
    assert (b.upper - b.lower) > 1.8 * naive


def test_block_bootstrap_coverage_with_serially_correlated_days():
    covered, reps = 0, 150
    for r in range(reps):
        rng = random.Random(100 + r)
        x, vals = 0.0, []
        for _ in range(300):
            x = 0.6 * x + rng.gauss(0, 1)
            vals.append(x)  # true mean 0, AR(1) dependence
        b = date_block_bootstrap(list(range(300)), vals, 300, block_length=15,
                                 resamples=800, seed=r)
        covered += b.lower <= 0 <= b.upper
    assert covered / reps >= 0.86


def test_bootstrap_is_seeded_and_flags_prereg_minimum():
    vals = [random.Random(4).gauss(0, 1) for _ in range(50)]
    idx = list(range(50))
    a = date_block_bootstrap(idx, vals, 50, block_length=5, resamples=500, seed=9)
    b = date_block_bootstrap(idx, vals, 50, block_length=5, resamples=500, seed=9)
    assert a == b and not a.prereg_compliant
    c = date_block_bootstrap(idx, vals, 50, block_length=5, resamples=10_000, seed=9)
    assert c.prereg_compliant


@pytest.mark.parametrize("kwargs", [
    dict(block_length=0), dict(block_length=60), dict(resamples=10),
])
def test_bootstrap_rejects_bad_inputs(kwargs):
    base = dict(block_length=5, resamples=500, seed=1)
    base.update(kwargs)
    with pytest.raises(ValueError):
        date_block_bootstrap([0, 1], [1.0, 2.0], 50, **base)
    with pytest.raises(ValueError):
        date_block_bootstrap([0, 99], [1.0, 2.0], 50, block_length=5,
                             resamples=500, seed=1)


# ----------------------------------------------------------------- placebo

def placebo_setup(mu, seed=0):
    jumps = [PlantedJump(s, 15 + k * 120 + 11 * n, mu)
             for n, s in enumerate(SYMS) for k in range(3)]
    data = synthetic_flat_bars(SYMS + ["SPY"], 400, 77 + seed, jumps=jumps)
    sessions = [b.day for b in data["SPY"]]

    def run(score_fn):
        return run_portfolio(data, "synthetic", SYMS, risk_fraction=.01,
                             slippage_bps=30, score_fn=score_fn)
    return run, sessions


def run_placebo(mu, seed, trials=100, **kw):
    run, sessions = placebo_setup(mu, seed)
    return placebo_test(marker_score_fn, run, random_entry_score_fn,
                        lambda r: trade_r_multiples(r["trades"]), sessions,
                        trials=trials, seed=5, pilot=8, **kw)


def test_placebo_passes_a_strong_planted_edge():
    res = run_placebo(0.08, 0)
    assert res.exposure_matched
    assert res.status == "pass"
    assert res.strategy_mean_r > res.placebo_p95
    assert res.placebo_exposure_band[0] <= res.strategy_exposure <= res.placebo_exposure_band[2]
    assert not res.prereg_compliant  # 100 < 10,000 trials


def test_placebo_false_positive_rate_is_near_five_percent():
    statuses = [run_placebo(0.0, seed, trials=60).status for seed in range(12)]
    assert all(s in ("pass", "fail") for s in statuses)
    # Binomial(12, .05): P(>= 4 passes) is about 0.002.
    assert statuses.count("pass") <= 3


def test_placebo_that_cannot_match_exposure_is_invalid_not_a_pass():
    res = run_placebo(0.08, 0, p_bounds=(1e-4, 2e-4))
    assert not res.exposure_matched
    assert res.status == "invalid_exposure_mismatch"


def test_exposure_fraction_counts_held_sessions():
    days = [date(2020, 1, d) for d in range(1, 11)]
    res = {"trades": [{"entry_day": "2020-01-02", "exit_day": "2020-01-04"},
                      {"entry_day": "2020-01-08", "exit_day": "2020-01-08"}]}
    assert exposure_fraction(res, days) == pytest.approx(0.4)


# --------------------------------------------------------- deflated Sharpe

def test_psr_known_value():
    # z = 0.1 * sqrt(999) / sqrt(1 + 0.5 * 0.01)
    z = 0.1 * math.sqrt(999) / math.sqrt(1.005)
    assert probabilistic_sharpe(0.1, 0.0, 1000, 0, 3) == pytest.approx(
        NormalDist().cdf(z), abs=1e-12)
    assert NormalDist().cdf(z) == pytest.approx(0.99919, abs=1e-5)


def test_expected_max_sharpe_matches_monte_carlo():
    rng = random.Random(8)
    mc = mean(max(rng.gauss(0, 1) for _ in range(100)) for _ in range(4000))
    assert expected_max_sharpe(100, 1.0) == pytest.approx(mc, rel=0.03)
    assert expected_max_sharpe(1, 1.0) == 0.0


def test_more_trials_deflate_more():
    rng = random.Random(9)
    rets = [rng.gauss(0.08, 1) for _ in range(500)]
    probs = [deflated_sharpe(rets, n_trials=n, var_trial_sharpes=0.002).probability
             for n in (1, 10, 80, 101, 1000)]
    assert probs == sorted(probs, reverse=True)


def test_best_of_many_noise_strategies_rarely_passes():
    rng = random.Random(10)
    false_pass, reps, n = 0, 120, 40
    for _ in range(reps):
        series = [[rng.gauss(0, 1) for _ in range(250)] for _ in range(n)]
        srs = [mean(s) / pstdev(s) for s in series]
        best = series[srs.index(max(srs))]
        v = pstdev(srs) ** 2
        false_pass += deflated_sharpe(best, n_trials=n, var_trial_sharpes=v).passes_95
    assert false_pass / reps <= 0.10
    # Without deflation (N=1) the same selection passes far more often.


def test_positive_autocorrelation_shrinks_effective_sample():
    rng = random.Random(11)
    x, rets = 0.0, []
    for _ in range(600):
        x = 0.5 * x + rng.gauss(0.05, 1)
        rets.append(x)
    d = deflated_sharpe(rets, n_trials=1, var_trial_sharpes=0.0)
    assert d.lag1_autocorrelation > 0.3
    assert d.effective_t < 0.5 * 600


def test_trial_count_uses_ledger_plus_new_ids():
    path = str(ROOT / "research/TRIAL_LEDGER.csv")
    assert ledger_trial_count(path) == 80
    assert ledger_trial_count(path, ["P3-0001", "P3-0002"]) == 82
    with pytest.raises(ValueError):
        ledger_trial_count(path, ["LEGACY-001"])
    with pytest.raises(ValueError):
        ledger_trial_count(path, ["X", "X"])


def test_per_trade_sharpes_available_for_legacy_trials():
    with zipfile.ZipFile(ROOT / "research/legacy/robustness-audit-38108710763.zip") as z:
        report = json.loads(z.read("robustness-report.json"))
    srs = per_trade_sharpes(legacy_rows(report))
    assert len(srs) == 80
    assert all(math.isfinite(s) for s in srs)


# ----------------------------------------------- matched-deposit benchmark

def bars_from(prices, start=date(2024, 1, 1)):
    out, d = [], start
    for o, c in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(DailyBar(d, o, max(o, c), min(o, c), c, 1e6))
        d += timedelta(days=1)
    return out


def test_align_moves_weekend_flows_to_next_session_and_rejects_late():
    bars = bars_from([(10, 10)] * 6)  # Mon 2024-01-01 .. Mon 2024-01-08
    sessions = [b.day for b in bars]
    aligned = align_cash_flows([(date(2024, 1, 6), 100.0),
                                (date(2024, 1, 7), 50.0)], sessions)
    assert aligned == {date(2024, 1, 8): 150.0}
    with pytest.raises(ValueError):
        align_cash_flows([(date(2024, 2, 1), 10.0)], sessions)
    with pytest.raises(ValueError):
        align_cash_flows([(date(2024, 1, 2), -5.0)], sessions)


def test_benchmark_price_only_is_flagged_incomplete_and_dividends_reinvest():
    bars = bars_from([(100, 100), (100, 110), (110, 110)])
    price_only = matched_deposit_benchmark(bars, 500, {}, dividends=None)
    assert not price_only.total_return_complete
    assert price_only.equity[-1] == pytest.approx(550)
    tr = matched_deposit_benchmark(bars, 500, {}, dividends={bars[1].day: 1.10})
    assert tr.total_return_complete
    # 5 shares + 5*1.10/110 = 5.05 shares at 110.
    assert tr.equity[-1] == pytest.approx(5.05 * 110)


def test_twr_ignores_deposits_and_mwr_rewards_good_timing():
    # Four sessions spread over a year so the annualized IRR is sane.
    days = [date(2024, 1, 2), date(2024, 4, 1), date(2024, 8, 1), date(2024, 12, 31)]
    bars = [DailyBar(d, o, max(o, c), min(o, c), c, 1e6) for d, (o, c) in
            zip(days, [(100, 100), (100, 90), (90, 120), (120, 120)])]
    none = matched_deposit_benchmark(bars, 500, {}, dividends={})
    flows = align_cash_flows([(days[2], 300.0)], days)
    dep = matched_deposit_benchmark(bars, 500, flows, dividends={})
    assert dep.total_deposits == 800
    assert dep.twr == pytest.approx(none.twr, abs=1e-12) == pytest.approx(0.2)
    mwr_none = money_weighted_return([(days[0], 500)], days[-1], none.equity[-1])
    mwr_dep = money_weighted_return([(days[0], 500), (days[2], 300)],
                                    days[-1], dep.equity[-1])
    assert mwr_dep > mwr_none  # bought the dip; TWR does not credit that


def test_mwr_known_value():
    r = money_weighted_return([(date(2023, 1, 1), 100.0)], date(2024, 1, 1), 110.0)
    assert r == pytest.approx(0.10, abs=1e-9)


def test_whole_share_benchmark_shows_cash_drag():
    bars = bars_from([(600, 600), (600, 660)])
    whole = matched_deposit_benchmark(bars, 500, {}, dividends={}, fractional=False)
    assert whole.equity[-1] == 500  # cannot afford one $600 share


def test_strategy_and_benchmark_receive_identical_deposits():
    data = synthetic_flat_bars(SYMS[:3] + ["SPY"], 120, 4)
    days = [b.day for b in data["SPY"]]
    aligned = align_cash_flows([(days[10] + timedelta(days=1), 100.0),
                                (days[60], 100.0)], days)
    strat = run_portfolio(data, "placebo", SYMS[:3], risk_fraction=.01,
                          score_fn=random_entry_score_fn(1, .3), cash_flows=aligned)
    bench = matched_deposit_benchmark(data["SPY"], 500, aligned, dividends={})
    assert [(d, a) for d, _, a in strat["cash_flow_events"]] == \
        [(d.isoformat(), a) for d, _, a in bench.flow_events]
    assert strat["total_deposits"] == bench.total_deposits - 500 == 200
    twr = time_weighted_return(500, [(v, a) for _, v, a in strat["cash_flow_events"]],
                               strat["final_equity"])
    assert strat["unit_nav_twr_pct"] == pytest.approx(100 * twr, abs=0.01)


def test_deposits_cannot_hide_drawdown_or_create_returns():
    data = synthetic_flat_bars(["AAA", "SPY"], 40, 3)
    days = [b.day for b in data["SPY"]]
    idle = run_portfolio(data, "idle", ["AAA"], score_fn=lambda *a: None,
                         cash_flows={days[5]: 100.0, days[20]: 100.0})
    assert idle["final_equity"] == 700
    assert idle["unit_nav_twr_pct"] == 0
    assert idle["max_daily_close_drawdown_pct"] == 0


def test_no_cash_flows_keeps_legacy_output_keys():
    data = synthetic_flat_bars(["AAA", "SPY"], 40, 3)
    out = run_portfolio(data, "idle", ["AAA"], score_fn=lambda *a: None)
    assert "cash_flow_events" not in out and "equity_curve" not in out
