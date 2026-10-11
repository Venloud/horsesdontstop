"""Phase 2 OFFLINE data-manifest preflight.

Never invokes Alpaca TradingClient, market APIs, Neon, or order submission.
A manifest with missing provenance MUST fail closed. This is a provenance
gate, not a certification that strategy statistics are valid.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any


APPROVED_STRATEGIES = (
    "momentum_volume", "trend_following", "high_52_week",
    "sector_leadership", "short_term_reversal",
    "etf_dual_momentum", "overnight_drift",
)
APPROVED_RULES = {
    "variants_total": 21,
    "risk_pct_per_order_max": 1,
    "historical_drawdown_pct_max": 15,
    "slippage_bps_per_side": 30,
    "stress_slippage_bps_per_side": 60,
    "low_frequency_history_years": 10,
    "trade_event_minimum": 300,
}
HEX64 = re.compile(r"^[a-f0-9]{64}$")
HEX40 = re.compile(r"^[a-f0-9]{40}$")
LEGACY_EXAMINED_THROUGH = date(2026, 10, 9)
FILES = ("bars", "universe_membership", "benchmark_total_return",
         "contributions")


def _parse_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return d if d.isoformat() == value else None


def _safe_asset(root: Path, path_text: Any) -> Path | None:
    """Only relative non-symlink paths inside the checked-out repository."""
    if not isinstance(path_text, str) or not path_text:
        return None
    rel = Path(path_text)
    if (rel.is_absolute() or not rel.parts or rel.parts[0] != "research"
            or ".." in rel.parts or any(p in (".", "") for p in rel.parts)):
        return None
    root = root.resolve()
    try:
        asset = root.joinpath(rel)
        if not asset.is_file() or asset.is_symlink():
            return None
        resolved = asset.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(65536):
            h.update(block)
    return h.hexdigest()


def _required_csv_headers(path: Path, required: set[str]) -> bool:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            first = next(reader, [])
            if len(first) != len(set(first)):
                return False
            return required.issubset(first) and next(reader, None) is not None
    except (UnicodeError, csv.Error, OSError):
        return False


def validate_manifest(manifest: Any, root: Path) -> list[str]:
    """Return stable blockers, never success unless EVERY item is documented.

    This gate checks actual on-disk artifact hashes, not just unverified
    hash text typed into the manifest. It does not validate point-in-time
    vendor licensing or prove absence of survivorship/lookahead bias.
    Those need human+integration evidence and independent harness tests.
    """
    blockers: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest_not_object"]
    if manifest.get("schema_version") != 1:
        blockers.append("schema_version_unsupported")
    if manifest.get("status") != "frozen":
        blockers.append("phase2_manifest_not_frozen")
    if manifest.get("phase0_reference") != "PR-9":
        blockers.append("phase0_pr_reference_mismatch")

    if manifest.get("strategies") != list(APPROVED_STRATEGIES):
        blockers.append("seven_strategy_ids_changed")
    rules = manifest.get("rules")
    if not isinstance(rules, dict):
        rules = {}
    for name, expected in APPROVED_RULES.items():
        val = rules.get(name)
        if type(val) is not int or val != expected:
            blockers.append("approved_rule_mismatch:" + name)

    revision = manifest.get("git_source_commit")
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        blockers.append("code_commit_not_pinned")

    assets = manifest.get("artifacts")
    if not isinstance(assets, dict):
        assets = {}
    headers = {
        "bars": {"symbol", "day", "open", "high", "low", "close", "volume"},
        "universe_membership": {"symbol", "first_eligible_day", "last_eligible_day"},
        "benchmark_total_return": {"day", "total_return_index"},
        "contributions": {"day", "amount_usd"},
    }
    resolved: dict[str, Path] = {}
    for key in FILES:
        item = assets.get(key)
        if not isinstance(item, dict):
            blockers.append("asset_missing:" + key)
            continue
        path = _safe_asset(root, item.get("path"))
        if path is None:
            blockers.append("asset_missing_or_unsafe:" + key)
            continue
        supplied = item.get("sha256")
        if not isinstance(supplied, str) or not HEX64.fullmatch(supplied):
            blockers.append("asset_hash_not_pinned:" + key)
            continue
        try:
            if _sha256(path) != supplied:
                blockers.append("asset_hash_mismatch:" + key)
                continue
            if not _required_csv_headers(path, headers[key]):
                blockers.append("asset_csv_schema_invalid:" + key)
                continue
        except OSError:
            blockers.append("asset_unreadable:" + key)
            continue
        resolved[key] = path

    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    for key in ("data_vendor", "feed", "fetch_timestamp_utc",
                "membership_source", "delisting_handling",
                "corporate_action_method", "dividend_treatment",
                "license_review"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            blockers.append("data_provenance_missing:" + key)
    if provenance.get("membership_verified_point_in_time") is not True:
        blockers.append("point_in_time_membership_not_verified")
    if provenance.get("survivorship_reviewed") is not True:
        blockers.append("survivorship_review_not_documented")

    folds = manifest.get("folds")
    if not isinstance(folds, dict):
        folds = {}
    ds = _parse_day(folds.get("discovery_start"))
    de = _parse_day(folds.get("discovery_end"))
    hs = _parse_day(folds.get("holdout_start"))
    he = _parse_day(folds.get("holdout_end"))
    if not all((ds, de, hs, he)) or not ds < de < hs <= he:
        blockers.append("invalid_chronological_fold_boundaries")
    elif hs <= LEGACY_EXAMINED_THROUGH:
        blockers.append("holdout_overlaps_previously_inspected_history")
    embargo = folds.get("embargo_sessions")
    if type(embargo) is not int or embargo < 5:
        blockers.append("embargo_sessions_not_fixed")
    if folds.get("holdout_inspected") is not False:
        blockers.append("holdout_not_verified_untouched")

    sampling = manifest.get("statistics")
    if not isinstance(sampling, dict):
        sampling = {}
    for key in ("bootstrap_seed", "placebo_seed"):
        val = sampling.get(key)
        if type(val) is not int or not 0 <= val < 2**32:
            blockers.append("statistical_seed_missing:" + key)
    if type(sampling.get("bootstrap_block_sessions")) is not int or (
            sampling.get("bootstrap_block_sessions", 0) < 1):
        blockers.append("bootstrap_block_not_fixed")
    if (type(sampling.get("bootstrap_replicates")) is not int
            or sampling.get("bootstrap_replicates", 0) < 10000):
        blockers.append("bootstrap_replicates_too_low")
    if (type(sampling.get("placebo_trials")) is not int
            or sampling.get("placebo_trials", 0) < 10000):
        blockers.append("placebo_trials_too_low")

    benchmark = manifest.get("benchmark")
    if not isinstance(benchmark, dict):
        benchmark = {}
    if benchmark.get("symbol") != "SPY":
        blockers.append("passive_benchmark_changed")
    if benchmark.get("matched_actual_contributions") is not True:
        blockers.append("actual_contributions_not_matched")
    if benchmark.get("comparison_contribution_asset") != "contributions":
        blockers.append("deposit_schedule_not_shared")
    if not isinstance(benchmark.get("execution_convention"), str) or not (
            benchmark.get("execution_convention") or "").strip():
        blockers.append("deposit_timing_convention_missing")

    if manifest.get("data_integrity_review_passed") is not True:
        blockers.append("data_integrity_review_outstanding")
    if manifest.get("harness_synthetic_selftests_passed") is not True:
        blockers.append("harness_selftests_outstanding")

    return blockers


def main() -> int:
    """CLI only reads a JSON manifest and local research files. Never trades."""
    root = Path(__file__).resolve().parents[1]
    file = root / "research" / "phase2_manifest.json"
    try:
        with file.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("PHASE 2 BLOCKED: manifest is missing or invalid")
        return 2
    reasons = validate_manifest(manifest, root)
    if reasons:
        print("PHASE 2 BLOCKED: " + ", ".join(reasons))
        print("No scored strategy runs and ZERO broker orders.")
        return 2
    print("PHASE 2 MANIFEST STRUCTURALLY VERIFIED. "
          "Independent data-bias review and scored-test approval still required.")
    print("ZERO broker orders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
