"""Offline validator for QuantConnect's summary-only coverage batch output.

Usage: python -m research.quantconnect.check_coverage_report <qc-output.txt>
No requests to QuantConnect, brokers, or Neon. An internally consistent
report is NOT independently authenticated vendor evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .phase2_coverage_batch import (
    ETFS_ORIGINAL_12, ETFS_PROPOSED_10, LIQUID_STOCKS, HISTORIC_DELISTED,
)

PREFIX = "QC_COVERAGE_V2 "
SYMBOLS = set(ETFS_ORIGINAL_12) | set(LIQUID_STOCKS) | set(HISTORIC_DELISTED)
EXPECTED_SAMPLES = ("2012_sample", "2016_sample", "2022_sample")
KINDS = {
    "protocol", "symbol", "event", "common_etf_coverage",
    "pit_universe_setup", "pit_universe_sample", "limitations", "done",
}


class CoverageReportError(ValueError):
    pass


def read_coverage_report(text):
    """Accept verbatim cloud cell output, but only parse tagged metadata.

    Untagged cloud runtime headers are ignored. Missing/truncated/doubled
    tagged evidence fails instead of being counted as a source success.
    """
    if not isinstance(text, str):
        raise CoverageReportError("report_not_text")
    records = []
    tagged_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(PREFIX):
            continue
        try:
            item = json.loads(line[len(PREFIX):])
        except json.JSONDecodeError as exc:
            raise CoverageReportError("invalid_json_record") from exc
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise CoverageReportError("unexpected_report_record")
        records.append(item)
        tagged_lines.append(line)
    if not records or records[0].get("kind") != "protocol" or (
            records[-1].get("kind") != "done"):
        raise CoverageReportError("report_missing_protocol_or_done")

    def only(kind):
        selected = [row for row in records if row["kind"] == kind]
        if len(selected) != 1:
            raise CoverageReportError("missing_or_duplicate_" + kind)
        return selected[0]

    protocol, limits, done = (
        only("protocol"), only("limitations"), only("done"))
    if (
        protocol.get("normalization_requested") != "TOTAL_RETURN"
        or protocol.get("normalization_independently_verified") is not False
        or protocol.get("fill_forward_requested") is not False
        or protocol.get("independent_exchange_calendar_verified") is not False
        or protocol.get("from_inclusive") != "1998-01-01"
        or protocol.get("to_exclusive") != "2026-10-01"
        or protocol.get("scored_backtest_run") is not False
        or protocol.get("raw_market_prices_exported") is not False
        or limits.get("point_in_time_survivorship_verified") is not False
        or limits.get("ten_year_strategy_eligible") is not False
        or limits.get("historical_membership_source_verified") is not False
        or done.get("symbol_records_expected") != 25
    ):
        raise CoverageReportError("protocol_or_limits_changed")

    symbols = [x for x in records if x["kind"] == "symbol"]
    if len(symbols) != 25 or {
            x.get("symbol") for x in symbols} != SYMBOLS:
        raise CoverageReportError("missing_duplicate_or_unknown_symbol")
    if any(x.get("status") not in (
            "bars_present", "add_failed", "history_failed", "no_bars")
           for x in symbols):
        raise CoverageReportError("unexpected_symbol_status")

    universes = [x for x in records if x["kind"] == "common_etf_coverage"]
    if len(universes) != 2 or {
            x.get("universe") for x in universes
    } != {"original_12", "proposed_10_unapproved"}:
        raise CoverageReportError("missing_or_duplicate_etf_comparison")
    comparisons = {x["universe"]: x for x in universes}
    for universe, tickers in (
        ("original_12", ETFS_ORIGINAL_12),
        ("proposed_10_unapproved", ETFS_PROPOSED_10),
    ):
        u = comparisons[universe]
        years = u.get("longest_consecutive_years")
        if (
            u.get("symbols") != list(tickers)
            or type(years) is not int or not 0 <= years <= 28
            or type(u.get("ten_year_session_coverage")) is not bool
            or u["ten_year_session_coverage"] != (years >= 10)
            or u.get("point_in_time_survivorship_verified") is not False
            or u.get("status") not in (
                "incomplete_symbols", "observed_not_pit_verified")
        ):
            raise CoverageReportError("invalid_etf_comparison")
    if any(
        s.get("status") != "bars_present"
        for s in symbols if s.get("symbol") in ETFS_ORIGINAL_12
    ) and comparisons["original_12"].get("ten_year_session_coverage"):
        raise CoverageReportError("missing_etf_with_positive_coverage")
    if any(
        s.get("status") != "bars_present"
        for s in symbols if s.get("symbol") in ETFS_PROPOSED_10
    ) and comparisons["proposed_10_unapproved"].get("ten_year_session_coverage"):
        raise CoverageReportError("missing_proposed_etf_with_positive_coverage")

    setup = only("pit_universe_setup")
    samples = [x for x in records if x["kind"] == "pit_universe_sample"]
    if (setup.get("status") not in ("ready", "failed")
            or setup.get("historic_universe_coverage_verified") is not False
            or (setup["status"] == "failed" and samples)
            or (setup["status"] == "ready"
                and (len(samples) != 3 or
                     [x.get("window") for x in samples] != list(EXPECTED_SAMPLES)))):
        raise CoverageReportError("invalid_pit_sample_protocol")
    for sample in samples:
        if (
            sample.get("status") not in ("sample_observed", "no_samples", "failed")
            or sample.get("historic_universe_coverage_verified") is not False
            or sample.get("survivorship_free_strategy_verified") is not False
        ):
            raise CoverageReportError("invalid_pit_sample")

    events = [x for x in records if x["kind"] == "event"]
    successful_stocks = {
        s["symbol"] for s in symbols
        if s["symbol"] not in ETFS_ORIGINAL_12
        and s["status"] == "bars_present"
    }
    expected_events = {
        (sym, kind) for sym in successful_stocks
        for kind in ("splits", "dividends", "delistings")
    }
    if len(events) != len(expected_events) or {
        (x.get("symbol"), x.get("type")) for x in events
    } != expected_events:
        raise CoverageReportError("missing_or_duplicate_corporate_event_query")
    if any(x.get("status") not in ("ok", "error")
           or (x["status"] == "ok" and
               (type(x.get("count")) is not int or x["count"] < 0))
           for x in events):
        raise CoverageReportError("invalid_corporate_event_result")

    blockers = [
        "no_independent_market_calendar",
        "no_independent_data_normalization_proof",
        "full_ten_year_point_in_time_universe_not_verified",
        "complete_delisting_and_vendor_rights_not_verified",
        "folds_and_holdout_not_frozen",
    ]
    for label in ("original_12", "proposed_10_unapproved"):
        if not comparisons[label]["ten_year_session_coverage"]:
            blockers.append(label + "_ten_year_common_etf_coverage_missing")
    if setup["status"] != "ready" or any(
            x["status"] != "sample_observed" for x in samples):
        blockers.append("sampled_historical_universe_access_unverified")
    if any(x["status"] == "error" for x in events):
        blockers.append("historical_corporate_event_query_errors")
    if any(x["status"] != "bars_present" for x in symbols):
        blockers.append("some_history_symbols_missing")

    return {
        "format": "QC_COVERAGE_V2",
        "report_integrity": "structurally_consistent_untrusted_cloud_text",
        "sha256_of_tagged_summary_lines": hashlib.sha256(
            ("\n".join(tagged_lines) + "\n").encode("utf-8")).hexdigest(),
        "symbol_records": len(symbols),
        "symbols_with_bars": sum(x["status"] == "bars_present" for x in symbols),
        "event_queries_ok": sum(x["status"] == "ok" for x in events),
        "event_queries_total": len(events),
        "historical_universe_sample_windows_observed": sum(
            x["status"] == "sample_observed" for x in samples),
        "original_12_common_full_years": comparisons["original_12"][
            "longest_consecutive_years"],
        "proposed_10_common_full_years": comparisons["proposed_10_unapproved"][
            "longest_consecutive_years"],
        "full_survivorship_aware_history_verified": False,
        "scored_phase3_authorized": False,
        "blockers": blockers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Unedited QuantConnect cell output")
    args = parser.parse_args()
    try:
        result = read_coverage_report(args.file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, CoverageReportError) as exc:
        print("QC COVERAGE INVALID: " + (
            str(exc) if isinstance(exc, CoverageReportError) else "cannot_read_file"))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
