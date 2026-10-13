"""PREREGISTRATION section 7 harness self-tests. Synthetic or archived data only.

No network, Alpaca or Neon. Nothing here scores a real strategy.
"""
import csv
import hashlib
import json
import math
import random
import zipfile
from datetime import date
from pathlib import Path

import pytest

from bot.portfolio_research import run_portfolio
from bot.research_harness import (
    PlantedJump, compare_rows, expected_r, legacy_replay, legacy_rows,
    ledger_vs_report, lookahead_audit, marker_score_fn, mean_ci,
    random_entry_score_fn, rerun_legacy_report, row_key, synthetic_flat_bars,
    trade_accounting_problems, trade_r_multiples,
)
from bot.strategies import DailyBar, STRATEGIES, signal_score

ROOT = Path(__file__).resolve().parents[1]
LEGACY_ZIP = ROOT / "research/legacy/robustness-audit-38108710763.zip"
# Digest GitHub reported for artifact 11690790832 of run 38108710763.
LEGACY_ZIP_SHA256 = "87d610ee15d6990296f3915ca2eed5040a81a106496f2bfafd530b03b39951cb"
SYMS = ["AAA", "BBB", "CCC", "DDD"]


def realistic_bars(symbols, sessions, seed):
    """Random-walk bars WITH wicks and noisy volume, so coded signals fire."""
    rng = random.Random(seed)
    days = [b.day for b in synthetic_flat_bars(["X"], sessions, 0)["X"]]
    out = {}
    for symbol in symbols:
        price, rows = 40.0 + 10 * rng.random(), []
        drift = rng.uniform(-0.0005, 0.0015)
        for day in days:
            o = price * math.exp(rng.gauss(0, 0.01))
            c = o * math.exp(rng.gauss(drift, 0.015))
            rows.append(DailyBar(day, o, max(o, c) * (1 + rng.random() * 0.01),
                                 min(o, c) * (1 - rng.random() * 0.01), c,
                                 1e6 * math.exp(rng.gauss(0, 0.4))))
            price = c
        out[symbol] = rows
    return out


# ---------------------------------------------------------------- hook

def test_score_fn_hook_leaves_legacy_path_identical():
    data = realistic_bars(["AAPL", "JPM", "XLK", "XLF", "SPY"], 330, 7)
    for strategy in ("momentum_volume", "trend_following"):
        legacy = run_portfolio(data, strategy, ["AAPL", "JPM"], start_index=60)
        hooked = run_portfolio(
            data, strategy, ["AAPL", "JPM"], start_index=60,
            score_fn=lambda sym, h, s, m, st=strategy: signal_score(st, h, s, m))
        assert legacy == hooked


def test_score_fn_requires_label_and_unknown_strategy_still_rejected():
    data = synthetic_flat_bars(SYMS + ["SPY"], 30, 1)
    with pytest.raises(ValueError):
        run_portfolio(data, "not_a_strategy", SYMS)
    with pytest.raises(ValueError):
        run_portfolio(data, "", SYMS, score_fn=marker_score_fn)


# ------------------------------------------------------ 1. future leaks

def leaky_factory(field):
    """Planted cheat: reads the ENTRY session's bar, unknown at its open."""
    def build(data):
        pos = {s: {b.day: i for i, b in enumerate(rows)} for s, rows in data.items()}

        def score(symbol, history, sector, market):
            i = pos[symbol][history[-1].day] + 1
            if i >= len(data[symbol]):
                return None
            today = data[symbol][i]
            if field == "close":
                return 1.0 if today.close > today.open else None
            return 1.0 if today.volume > history[-1].volume else None
        return score
    return build


@pytest.mark.parametrize("field", ["close", "volume"])
def test_planted_future_leak_is_caught(field):
    data = realistic_bars(["AAPL", "JPM", "SPY"], 160, 11)
    build = leaky_factory(field)

    def run(d):
        return run_portfolio(d, "planted_leak", ["AAPL", "JPM"],
                             start_index=20, score_fn=build(d))
    days = [b.day for b in data["SPY"]]
    # Dense checkpoints: a leak only shows on days a decision is made.
    violations = lookahead_audit(data, run, days[40:150], seed=3)
    assert violations, "a signal reading the entry-day bar must be flagged"
    assert all(v["future_information_used"] for v in violations)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_coded_strategies_pass_lookahead_audit(strategy):
    symbols = ["AAPL", "JPM", "MSFT", "BAC"]
    data = realistic_bars(symbols + ["XLK", "XLF", "SPY"], 340, 5)

    def run(d):
        return run_portfolio(d, strategy, symbols, start_index=60)
    base = run(data)
    assert base["trade_count"] > 0, "audit would be vacuous without trades"
    days = [b.day for b in data["SPY"]]
    assert lookahead_audit(data, run, days[80:330:2], seed=9) == []


def test_placebo_and_marker_signals_pass_lookahead_audit():
    data = synthetic_flat_bars(SYMS + ["SPY"], 200, 2,
                               jumps=[PlantedJump("AAA", 50, .02)])
    days = [b.day for b in data["SPY"]]
    for fn in (marker_score_fn, random_entry_score_fn(4, .3)):
        def run(d, fn=fn):
            return run_portfolio(d, "synthetic", SYMS, score_fn=fn)
        assert run(data)["trade_count"] > 0
        assert lookahead_audit(data, run, days[20:190:2], seed=1) == []


# ---------------------------------------------- 2. random entries vs cost

def pooled_random_r(slippage_bps, reps, seed0=0):
    out = []
    for seed in range(seed0, seed0 + reps):
        data = synthetic_flat_bars(SYMS + ["SPY"], 400, seed)
        r = run_portfolio(data, "random_placebo", SYMS, risk_fraction=.01,
                          slippage_bps=slippage_bps,
                          score_fn=random_entry_score_fn(seed, .2))
        out += trade_r_multiples(r["trades"])
    return out


def test_analytic_cost_of_random_entries():
    # (1 - s) / (1 + s) - 1 per trade, in units of the 3% initial stop.
    assert expected_r(0) == 0
    assert expected_r(30) == pytest.approx(-0.19940, abs=1e-5)
    assert expected_r(60) == pytest.approx(-0.39761, abs=1e-5)


@pytest.mark.parametrize("slip", [30, 60])
def test_random_entries_center_on_negative_costs(slip):
    rs = pooled_random_r(slip, 160)
    m, lo, hi, se = mean_ci(rs)
    assert len(rs) > 4000
    assert abs(m - expected_r(slip)) < 4 * se
    assert hi < 0, "random entries must not look profitable after costs"


def test_random_entries_without_costs_center_on_zero():
    m, lo, hi, se = mean_ci(pooled_random_r(0, 120, seed0=500))
    assert abs(m) < 4 * se


# ------------------------------------------------ 3. planted known edge

MU = 0.02


def planted_run(seed, mu=MU):
    jumps = [PlantedJump(s, 20 + k * 120 + 5 * n, mu)
             for n, s in enumerate(SYMS) for k in range(3)]
    data = synthetic_flat_bars(SYMS + ["SPY"], 400, 10_000 + seed, jumps=jumps)
    result = run_portfolio(data, "planted_edge", SYMS, risk_fraction=.01,
                           slippage_bps=30, score_fn=marker_score_fn)
    days = {b.day.isoformat(): i for i, b in enumerate(data["SPY"])}
    for t in result["trades"]:
        e, x = days[t["entry_day"]], days[t["exit_day"]]
        # Exactness needs each trade to carry exactly ONE planted move.
        assert e - 1 in {j.signal_index for j in jumps if j.symbol == t["symbol"]}
        others = [j for j in jumps if j.symbol == t["symbol"]
                  and j.signal_index != e - 1 and e <= j.signal_index + 1 < x]
        assert not others
    return trade_r_multiples(result["trades"])


def test_planted_edge_recovered_inside_confidence_interval():
    rs = [r for seed in range(150) for r in planted_run(seed)]
    m, lo, hi, se = mean_ci(rs)
    truth = expected_r(30, MU)
    assert truth == pytest.approx(0.46995, abs=1e-4)
    assert lo <= truth <= hi
    assert lo > 0 > expected_r(30), "edge must separate from the cost null"


def test_planted_edge_interval_is_calibrated():
    truth = expected_r(30, MU)
    covered = 0
    blocks = 50
    for b in range(blocks):
        rs = [r for seed in range(b * 10, b * 10 + 10)
              for r in planted_run(1000 + seed)]
        m, lo, hi, se = mean_ci(rs)
        covered += lo <= truth <= hi
    # Nominal 95%; binomial(50, .95) is >= 43 with probability > .99.
    assert covered >= 43


# ------------------------------------------- 4. legacy run 38108710763

def legacy_report():
    raw = LEGACY_ZIP.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEGACY_ZIP_SHA256
    with zipfile.ZipFile(LEGACY_ZIP) as z:
        return json.loads(z.read("robustness-report.json"))


def test_trial_ledger_equals_archived_legacy_report():
    report = legacy_report()
    with open(ROOT / "research/TRIAL_LEDGER.csv", newline="") as f:
        ledger = list(csv.DictReader(f))
    assert len(ledger) == 80 == len(legacy_rows(report))
    assert ledger_vs_report(ledger, report) == []


def test_archived_legacy_trades_reproduce_their_summaries():
    rows = legacy_rows(report := legacy_report())
    assert sum(len(r["trades"]) for r in rows) == 2192
    assert report["selected_symbols"][0] == "AAPL"
    for row in rows:
        assert trade_accounting_problems(row) == [], row_key(row)


def test_full_legacy_replay_is_blocked_without_original_bars():
    status, problems = legacy_replay(None, legacy_report())
    assert status == "blocked_bars_snapshot_missing"


def synthetic_legacy_data():
    symbols = ["AAPL", "MSFT", "JPM", "BAC", "XOM"]
    data = realistic_bars(symbols + ["XLK", "XLF", "XLE", "SPY"], 700, 21)
    return symbols, data


def test_replay_machinery_matches_itself_and_flags_one_changed_bar():
    symbols, data = synthetic_legacy_data()
    report = rerun_legacy_report(data, symbols)
    rows = legacy_rows(report)
    assert len(rows) == 80
    status, problems = legacy_replay(data, report)
    assert (status, problems) == ("replayed_to_rounding", [])

    traded = {t["symbol"] for r in rows for t in r["trades"]}
    target = sorted(traded)[0]
    bar_i = 500
    old = data[target][bar_i]
    data[target][bar_i] = DailyBar(old.day, old.open * 0.8, old.high,
                                   old.low * 0.8, old.close, old.volume)
    status, problems = legacy_replay(data, report)
    assert status == "replay_mismatch" and problems


def test_compare_rows_tolerates_only_rounding():
    a = [{"design": "d", "window": "w", "strategy": "s", "risk_pct": 1.0,
          "slippage_bps": 30.0, "return_pct": 1.23, "trades": []}]
    b = [dict(a[0], return_pct=1.235)]
    c = [dict(a[0], return_pct=1.25)]
    assert compare_rows(a, b, fields=("return_pct",)) == []
    assert compare_rows(a, c, fields=("return_pct",))
