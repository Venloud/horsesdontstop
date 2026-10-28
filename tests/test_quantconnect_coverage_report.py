"""Offline-only tests of the QuantConnect coverage output ingestion path."""
import json

import pytest

from research.quantconnect.check_coverage_report import (
    PREFIX, CoverageReportError, read_coverage_report,
)
from research.quantconnect.phase2_coverage_batch import (
    ETFS_ORIGINAL_12, ETFS_PROPOSED_10, LIQUID_STOCKS, HISTORIC_DELISTED,
)


def fake_cloud_rows():
    rows = [{
        "kind": "protocol",
        "from_inclusive": "1998-01-01",
        "to_exclusive": "2026-10-01",
        "normalization_requested": "TOTAL_RETURN",
        "normalization_independently_verified": False,
        "fill_forward_requested": False,
        "independent_exchange_calendar_verified": False,
        "scored_backtest_run": False,
        "raw_market_prices_exported": False,
    }]
    for sym in (*ETFS_ORIGINAL_12, *LIQUID_STOCKS, *HISTORIC_DELISTED):
        rows.append({"kind": "symbol", "symbol": sym, "status": "bars_present",
                     "group": "etf" if sym in ETFS_ORIGINAL_12 else "stock",
                     "daily_sessions": 2500})
        if sym not in ETFS_ORIGINAL_12:
            for kind in ("splits", "dividends", "delistings"):
                rows.append({"kind": "event", "symbol": sym, "type": kind,
                             "status": "ok", "count": 2})
    for name, syms, years in (
        ("original_12", ETFS_ORIGINAL_12, 7),
        ("proposed_10_unapproved", ETFS_PROPOSED_10, 14),
    ):
        rows.append({
            "kind": "common_etf_coverage", "universe": name,
            "status": "observed_not_pit_verified",
            "symbols": list(syms), "full_calendar_years": list(range(2012,2012+years)),
            "longest_consecutive_years": years,
            "ten_year_session_coverage": years >= 10,
            "point_in_time_survivorship_verified": False,
        })
    rows.append({
        "kind": "limitations",
        "point_in_time_survivorship_verified": False,
        "ten_year_strategy_eligible": False,
        "historical_membership_source_verified": False,
    })
    rows.append({"kind": "pit_universe_setup", "status": "ready",
                 "historic_universe_coverage_verified": False})
    for label in ("2012_sample", "2016_sample", "2022_sample"):
        rows.append({
            "kind": "pit_universe_sample", "window": label,
            "status": "sample_observed",
            "sampled_sessions": 9,
            "historic_universe_coverage_verified": False,
            "survivorship_free_strategy_verified": False,
        })
    rows.append({"kind": "done", "symbol_records_expected": 25})
    return rows


def make_text(rows):
    return "\n".join(
        ["Unrelated QuantConnect notebook progress header"]
        + [PREFIX + json.dumps(x, sort_keys=True) for x in rows]
        + [""])


def test_fictional_14yr_candidate_never_proves_pit_or_authorizes_scoring():
    verdict = read_coverage_report(make_text(fake_cloud_rows()))
    assert verdict["symbol_records"] == 25
    assert verdict["symbols_with_bars"] == 25
    assert verdict["event_queries_ok"] == 39
    assert verdict["historical_universe_sample_windows_observed"] == 3
    assert verdict["original_12_common_full_years"] == 7
    assert verdict["proposed_10_common_full_years"] == 14
    assert verdict["full_survivorship_aware_history_verified"] is False
    assert verdict["scored_phase3_authorized"] is False
    assert "original_12_ten_year_common_etf_coverage_missing" in verdict["blockers"]
    assert len(verdict["sha256_of_tagged_summary_lines"]) == 64


def test_missing_done_or_duplicate_symbol_invalidates_report():
    rows = fake_cloud_rows()
    with pytest.raises(CoverageReportError, match="done"):
        read_coverage_report(make_text(rows[:-1]))
    rows = fake_cloud_rows()
    rows.insert(5, rows[5].copy())
    with pytest.raises(CoverageReportError, match="symbol"):
        read_coverage_report(make_text(rows))


def test_altered_fill_forward_or_unverified_flags_are_refused():
    for field, val in (
        ("fill_forward_requested", True),
        ("normalization_requested", "RAW"),
        ("normalization_independently_verified", True),
        ("scored_backtest_run", True),
    ):
        rows = fake_cloud_rows()
        rows[0][field] = val
        with pytest.raises(CoverageReportError, match="protocol"):
            read_coverage_report(make_text(rows))


def test_broken_event_record_and_pit_sample_not_counted_as_success():
    rows = fake_cloud_rows()
    rows[:] = [r for r in rows if not (
        r["kind"] == "event" and r["symbol"] == "FRC"
        and r["type"] == "delistings")]
    with pytest.raises(CoverageReportError, match="event"):
        read_coverage_report(make_text(rows))
    rows = fake_cloud_rows()
    rows[-2]["survivorship_free_strategy_verified"] = True
    with pytest.raises(CoverageReportError, match="pit_sample"):
        read_coverage_report(make_text(rows))


def test_provider_errors_preserved_as_blockers_not_silent_success():
    rows = fake_cloud_rows()
    for row in rows:
        if row["kind"] == "symbol" and row["symbol"] == "ENRNQ":
            row.update({"status": "history_failed", "error_type": "ValueError"})
        if row["kind"] == "event" and row["symbol"] == "ENRNQ":
            row["status"] = "error"
            row["error_type"] = "ValueError"
            row.pop("count", None)
    rows = [row for row in rows
            if not (row["kind"] == "event" and row["symbol"] == "ENRNQ")]
    verdict = read_coverage_report(make_text(rows))
    assert verdict["symbols_with_bars"] == 24
    assert "some_history_symbols_missing" in verdict["blockers"]


def test_untrusted_cloud_text_never_contains_raw_bar_data_or_forced_verified_status():
    rows = fake_cloud_rows()
    digest1 = read_coverage_report(make_text(rows))[
        "sha256_of_tagged_summary_lines"]
    digest2 = read_coverage_report(make_text(rows))[
        "sha256_of_tagged_summary_lines"]
    assert digest1 == digest2
    assert "vendor_verified" not in str(read_coverage_report(make_text(rows)))
