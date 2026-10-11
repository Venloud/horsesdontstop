"""Offline-only integrity checks for owner-approved Phase 0 research docs.

These tests inspect CSV/Markdown, never instantiate a trading client,
never connect to Neon, and never create brokerage orders.
"""
import csv
from collections import Counter
from itertools import product
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "research" / "TRIAL_LEDGER.csv"
SPEC = ROOT / "docs" / "PREREGISTRATION.md"
GUIDE = ROOT / "docs" / "TRIAL_LEDGER.md"

STRATEGIES = (
    "momentum_volume", "trend_following", "high_52_week",
    "sector_leadership",
)
SEVEN_CANDIDATES = (*STRATEGIES,
                    "short_term_reversal", "etf_dual_momentum",
                    "overnight_drift")


def records():
    with LEDGER.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def test_registered_seven_candidates_and_approved_pass_gates():
    document = SPEC.read_text(encoding="utf-8")
    assert all(f"`{name}`" in document for name in SEVEN_CANDIDATES)
    assert "**21 parameter variants**" in document
    assert "**300 sufficiently independent" in document
    assert "10 calendar years" in document
    assert "+0.15R" in document
    assert "95% date-block bootstrap lower confidence bound" in document
    assert "95th percentile" in document
    assert "deflated Sharpe" in document
    assert "≥95%" in document
    assert "≥60%" in document
    assert "2022" in document
    assert "≥50%" in document
    assert "60bps per side" in document
    assert "drawdown ≤15% at 1%" in document
    assert "same actual contribution amounts on the same dates" in document
    assert "overnight_drift" in document
    assert "before" in document.lower()
    assert "scored" in document.lower()
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED=False" in document
    assert "kill_switch_active=TRUE" in document
    assert "submissions_enabled=FALSE" in document
    assert "never authorizes actual paper orders" in document.lower()
    assert "owner-approved" in document.lower()
    assert "30 basis points per side" in document
    assert "60 bps round-trip slippage" in document
    assert "21 variants total" in document
    assert GUIDE.exists()


def test_archive_exactly_80_logged_legacy_trials_with_no_duplicates():
    rows = records()
    assert len(rows) == 80
    assert [r["trial_id"] for r in rows] == [
        f"LEGACY-{i:03d}" for i in range(1, 81)
    ]
    assert len({r["trial_id"] for r in rows}) == 80
    assert all(r["source_run_id"] == "38108710763" for r in rows)
    assert all(r["source_job_id"] == "114379585494" for r in rows)
    assert all(r["validation_status"] ==
               "legacy_exploratory_preinspected" for r in rows)
    assert all(r["prior_results_seen"] == "true" for r in rows)
    assert all(r["universe"] == "BROADER_SYMBOLS_15_SURVIVORS"
               for r in rows)
    assert all(r["first_day"] <= r["last_day"] for r in rows)
    assert all(float(r["final_equity_usd"]) > 0 for r in rows)
    assert all(int(r["trade_count"]) >= 0 for r in rows)
    assert all(float(r["max_daily_close_drawdown_pct"]) >= 0
               for r in rows)


def test_every_historical_variant_present_exactly_once():
    rows = records()
    by_design = Counter(r["design"] for r in rows)
    assert by_design == {
        "independent_reset_window": 64,
        "continuous_no_resets": 16,
    }
    configs = {
        (r["design"], r["window"], r["strategy"],
         int(r["risk_pct"]), int(r["slippage_bps_per_side"]))
        for r in rows
    }
    expected = {
        ("independent_reset_window", f"window_{window}", strategy,
         risk, slip)
        for window, strategy, risk, slip in product(
            range(1, 5), STRATEGIES, (1, 2), (10, 30))
    }
    expected |= {
        ("continuous_no_resets", "full_continuous", strategy, risk, slip)
        for strategy, risk, slip in product(
            STRATEGIES, (1, 2), (10, 30))
    }
    assert len(configs) == len(expected)
    assert configs == expected


def test_known_high_profile_historical_figures_match_ci_log():
    rows = records()
    picked = [
        x for x in rows
        if x["design"] == "continuous_no_resets"
        and x["strategy"] == "momentum_volume"
        and x["risk_pct"] == "2"
    ]
    assert len(picked) == 2
    by_slip = {int(row["slippage_bps_per_side"]): row for row in picked}
    assert float(by_slip[10]["final_equity_usd"]) == 886.99
    assert float(by_slip[10]["return_pct"]) == 77.4
    assert int(by_slip[10]["trade_count"]) == 51
    assert float(by_slip[30]["final_equity_usd"]) == 799.58
    assert float(by_slip[30]["return_pct"]) == 59.92
    assert all(row["first_day"] == "2024-10-24" for row in picked)
    assert all(row["last_day"] == "2026-10-09" for row in picked)
    assert all(row["sessions"] == "491" for row in picked)


def test_docs_make_legacy_bias_and_no_trade_claims_explicit():
    guide = GUIDE.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    assert "64" in guide and "16" in guide
    assert "not 80 independent datasets" in guide.lower()
    assert "not alpaca-paper account profits" in guide.lower()
    assert "do not" in guide.lower()
    assert "already inspected" in spec.lower()
    assert "never authorizes actual paper orders" in spec.lower()
    assert "30bps adverse entry + 30bps adverse exit" in spec.lower()
    assert "10-calendar-year low-frequency exception" in spec.lower()
    assert "no agent may invent a tie-break" in spec.lower()
