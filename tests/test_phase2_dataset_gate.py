"""Offline tests for Phase 2's deliberately fail-closed data gate."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from bot.phase2_dataset_gate import (
    APPROVED_RULES, APPROVED_STRATEGIES,
    _safe_asset, validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads(
    (ROOT / "research" / "phase2_manifest.json").read_text()
)


def write_artifacts(root):
    files = {
        "bars": ("research/data/bars.csv",
                 "symbol,day,open,high,low,close,volume\n"
                 "SPY,2026-10-13,100,105,98,101,10000\n"),
        "universe_membership": ("research/data/universe.csv",
                 "snapshot_day,known_at_utc,snapshot_id,security_id,"
                 "symbol,eligible,source_id,complete\n"
                 "2026-10-12,2026-10-12T20:10:00Z,s1,QC:SPY,"
                 "SPY,true,synthetic_fixture,true\n"),
        "benchmark_total_return": ("research/data/benchmark.csv",
                 "day,total_return_index\n"
                 "2026-10-13,100.001\n"),
        "contributions": ("research/data/contributions.csv",
                 "day,amount_usd\n"
                 "2026-10-13,0\n"),
    }
    result = {}
    for name, (relpath, body) in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        result[name] = {
            "path": relpath, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        }
    return result


def synthetic_manifest(tmp_path):
    """Complete synthetic fixture tests STRUCTURE only, not real data proof."""
    m = copy.deepcopy(TEMPLATE)
    m["status"] = "frozen"
    m["git_source_commit"] = "a" * 40
    m["artifacts"] = write_artifacts(tmp_path)
    m["provenance"].update(
        data_vendor="Synthetic test ONLY",
        feed="synthetic",
        fetch_timestamp_utc="2026-10-11T10:00:00+00:00",
        membership_source="synthetic fixture",
        delisting_handling="synthetic no delistings",
        corporate_action_method="synthetic no actions",
        dividend_treatment="synthetic total return",
        license_review="synthetic-only fixture",
        membership_verified_point_in_time=True,
        survivorship_reviewed=True,
    )
    m["folds"].update(
        discovery_start="2020-01-01", discovery_end="2026-10-01",
        holdout_start="2026-10-13", holdout_end="2027-11-01",
        embargo_sessions=5, holdout_inspected=False,
    )
    m["statistics"].update(
        bootstrap_seed=312, placebo_seed=313,
        bootstrap_block_sessions=20,
    )
    m["benchmark"]["execution_convention"] = (
        "For both accounts, invest at next eligible session open")
    m["data_integrity_review_passed"] = True
    m["harness_synthetic_selftests_passed"] = True
    return m


def test_shipped_template_fails_closed_and_preserves_approved_values():
    assert TEMPLATE["status"] == "draft"
    assert tuple(TEMPLATE["strategies"]) == APPROVED_STRATEGIES
    assert TEMPLATE["rules"] == APPROVED_RULES
    blockers = validate_manifest(TEMPLATE, ROOT)
    assert "phase2_manifest_not_frozen" in blockers
    assert "code_commit_not_pinned" in blockers
    assert "asset_missing_or_unsafe:bars" in blockers
    assert "data_integrity_review_outstanding" in blockers
    assert "harness_selftests_outstanding" in blockers
    assert len(blockers) >= 15


def test_complete_synthetic_fixture_only_passes_structural_checks(tmp_path):
    assert validate_manifest(synthetic_manifest(tmp_path), tmp_path) == []


@pytest.mark.parametrize("key,value", [
    ("variants_total", 22),
    ("risk_pct_per_order_max", 2),
    ("historical_drawdown_pct_max", 25),
    ("slippage_bps_per_side", 10),
    ("stress_slippage_bps_per_side", 30),
    ("low_frequency_history_years", 15),
    ("trade_event_minimum", 200),
    ("risk_pct_per_order_max", True),
])
def test_approved_rules_cannot_be_modified(tmp_path, key, value):
    m = synthetic_manifest(tmp_path)
    m["rules"][key] = value
    assert "approved_rule_mismatch:" + key in validate_manifest(m, tmp_path)


def test_retroactive_first_last_listing_csv_is_not_a_valid_manifest(tmp_path):
    m = synthetic_manifest(tmp_path)
    universe = tmp_path / m["artifacts"]["universe_membership"]["path"]
    universe.write_text(
        "symbol,first_eligible_day,last_eligible_day\\n"
        "FRC,1986-01-01,2023-05-03\\n",
        encoding="utf-8",
    )
    m["artifacts"]["universe_membership"]["sha256"] = hashlib.sha256(
        universe.read_bytes()).hexdigest()
    assert "asset_csv_schema_invalid:universe_membership" in (
        validate_manifest(m, tmp_path))


def test_removed_overnight_drift_rejected(tmp_path):
    m = synthetic_manifest(tmp_path)
    m["strategies"] = m["strategies"][:-1]
    assert "seven_strategy_ids_changed" in validate_manifest(m, tmp_path)


def test_unsafe_or_nonexistent_artifact_path_rejected(tmp_path):
    m = synthetic_manifest(tmp_path)
    for path in ("../../etc/passwd", "/etc/passwd", "research/none.csv",
                 "./", ".", "research/../elsewhere.csv"):
        m["artifacts"]["bars"]["path"] = path
        assert "asset_missing_or_unsafe:bars" in validate_manifest(m, tmp_path)


def test_artifact_hash_mismatch_detects_mutation(tmp_path):
    m = synthetic_manifest(tmp_path)
    path = tmp_path / m["artifacts"]["bars"]["path"]
    path.write_text(path.read_text() + "SPY,2026-10-14,1,1,1,1,1\n")
    assert "asset_hash_mismatch:bars" in validate_manifest(m, tmp_path)


def test_discovery_holdout_must_be_ordered_untouched_and_new(tmp_path):
    m = synthetic_manifest(tmp_path)
    m["folds"]["holdout_start"] = "2026-10-01"
    assert "holdout_overlaps_previously_inspected_history" in (
        validate_manifest(m, tmp_path))
    m["folds"]["holdout_start"] = "2026-10-13"
    m["folds"]["holdout_inspected"] = True
    assert "holdout_not_verified_untouched" in validate_manifest(m, tmp_path)
    m["folds"]["holdout_inspected"] = False
    m["folds"]["embargo_sessions"] = 0
    assert "embargo_sessions_not_fixed" in validate_manifest(m, tmp_path)


def test_statistical_seed_and_replicates_must_be_frozen(tmp_path):
    m = synthetic_manifest(tmp_path)
    m["statistics"]["bootstrap_seed"] = None
    m["statistics"]["placebo_trials"] = 999
    reasons = validate_manifest(m, tmp_path)
    assert "statistical_seed_missing:bootstrap_seed" in reasons
    assert "placebo_trials_too_low" in reasons


def test_benchmark_requires_actual_matching_deposits(tmp_path):
    m = synthetic_manifest(tmp_path)
    m["benchmark"]["matched_actual_contributions"] = False
    assert "actual_contributions_not_matched" in validate_manifest(m, tmp_path)
    m["benchmark"]["matched_actual_contributions"] = True
    m["benchmark"]["comparison_contribution_asset"] = "hypothetical_100usd"
    assert "deposit_schedule_not_shared" in validate_manifest(m, tmp_path)


def test_no_point_in_time_universe_provenance_cannot_pass(tmp_path):
    m = synthetic_manifest(tmp_path)
    m["provenance"]["membership_verified_point_in_time"] = False
    assert "point_in_time_membership_not_verified" in (
        validate_manifest(m, tmp_path))


def test_everything_is_read_only_and_does_not_import_brokerage_client():
    source = (ROOT / "bot" / "phase2_dataset_gate.py").read_text()
    assert "from alpaca." not in source
    assert "import alpaca" not in source
    assert ".submit_order(" not in source
    assert "run_portfolio(" not in source
    assert ".execute(" not in source
    assert ".run_sql(" not in source
