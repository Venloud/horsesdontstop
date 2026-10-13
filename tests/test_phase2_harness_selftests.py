"""Preregistration section 7: synthetic-only harness checks.

A true replay of original run 38108710763 remains BLOCKED until its
original underlying market-bar snapshot and hashes exist. Output rows
alone cannot demonstrate replay.
"""
import csv
import random
from math import sqrt
from pathlib import Path

import pytest

from bot.phase2_harness_selftests import (
    FutureInformationLeak, SyntheticDecision,
    legacy_replay_prerequisites, synthetic_mean_net_bps,
    synthetic_open_to_open,
)

SLIP = .003


def test_deliberately_future_leaking_signal_is_flagged():
    prices = [100.0, 100.0, 110.0, 100.0]
    legitimate = SyntheticDecision(entry_index=2, exit_index=3,
                                   last_known_index=1)
    assert len(synthetic_open_to_open(prices, [legitimate])) == 1
    for known_index in (2, 3, 4):
        leaking = SyntheticDecision(entry_index=2, exit_index=3,
                                    last_known_index=known_index)
        with pytest.raises(
            FutureInformationLeak, match="future_information_used_for_entry"
        ):
            synthetic_open_to_open(prices, [leaking])


@pytest.mark.parametrize("seed", [8, 24, 193, 2026])
def test_random_null_entries_cluster_at_exact_negative_cost(seed):
    # The market has zero movement; timing and instrument choice cannot
    # create an edge. Random non-overlapping open-to-open positions pay
    # 30bps on BOTH entry and exit, with no invented extra fees.
    rng = random.Random(seed)
    prices = [100.0] * 1202
    entry_indices = sorted(rng.sample(range(1, 1201, 3), 120))
    decisions = [
        SyntheticDecision(i, i + 1, i - 1) for i in entry_indices
    ]
    trades = synthetic_open_to_open(prices, decisions)
    analytical_cost_bps = (
        ((1 - SLIP) / (1 + SLIP)) - 1
    ) * 10000
    assert len(trades) == 120
    assert -61 < analytical_cost_bps < -59
    assert synthetic_mean_net_bps(trades) == pytest.approx(
        analytical_cost_bps, abs=1e-9)


def test_planted_positive_edge_recovered_with_calibrated_95pct_band():
    # Independently scripted price changes, not real performance.
    # Each known synthetic regime produces E[exit open] = 101 vs
    # entry open 100, plus seeded zero-mean Gaussian noise. The
    # analytic 95%-of-sampling-distribution band is preregistered here.
    rng = random.Random(8891)
    opens = [100.0, 100.0]
    decisions = []
    n = 500
    population_price_noise_sd = 0.20
    for _ in range(n):
        entry = len(opens)
        opens.extend([100.0, 101.0 + rng.gauss(0, population_price_noise_sd)])
        decisions.append(SyntheticDecision(entry, entry + 1, entry - 1))
    observed_bps = synthetic_mean_net_bps(
        synthetic_open_to_open(opens, decisions))
    expected_bps = (101.0 * (1 - SLIP) /
                    (100.0 * (1 + SLIP)) - 1) * 10000
    sd_bps = (population_price_noise_sd * (1 - SLIP) /
              (100.0 * (1 + SLIP))) * 10000
    half_width = 1.96 * sd_bps / sqrt(n)
    assert expected_bps > 0
    assert expected_bps - half_width <= observed_bps <= expected_bps + half_width


def test_nonstandard_cost_or_overlapping_trades_are_rejected():
    with pytest.raises(ValueError, match="slippage"):
        synthetic_open_to_open([100, 100, 100], [], slippage_bps_per_side=0)
    with pytest.raises(ValueError, match="Overlapping"):
        synthetic_open_to_open(
            [100] * 6,
            [SyntheticDecision(1, 3, 0), SyntheticDecision(3, 4, 2)],
        )


def test_legacy_report_rows_are_archived_but_not_mistaken_for_replay():
    path = Path(__file__).resolve().parents[1] / "research" / "TRIAL_LEDGER.csv"
    with path.open(newline="", encoding="utf8") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 80
    assert len({row["trial_id"] for row in rows}) == 80
    assert {row["source_run_id"] for row in rows} == {"38108710763"}
    assert all("legacy_exploratory" in row["validation_status"] for row in rows)
    # This is an OUTPUT-LEDGER integrity check, NOT a replay of raw bars.


def test_true_legacy_replay_remains_blocked_without_identical_raw_market_bars():
    assert set(legacy_replay_prerequisites(
        original_market_bars_sha256=None,
        archived_market_bars_sha256=None,
        original_code_commit_sha=None,
    )) == {
        "original_market_data_hash_missing",
        "archived_market_data_hash_missing",
        "original_code_commit_missing",
    }
    assert legacy_replay_prerequisites(
        original_market_bars_sha256="1" * 64,
        archived_market_bars_sha256="2" * 64,
        original_code_commit_sha="3" * 40,
    ) == ("market_data_hash_mismatch",)


def test_synthetic_probe_never_imports_broker_or_connects_to_neon():
    content = (
        Path(__file__).resolve().parents[1] /
        "bot" / "phase2_harness_selftests.py"
    ).read_text()
    for blocked in ("TradingClient", "submit_order(", "cancel_order(",
                    "psycopg", "alpaca.", "neon.connect"):
        assert blocked not in content
