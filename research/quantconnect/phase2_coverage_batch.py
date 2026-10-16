"""QuantConnect FREE-cloud QuantBook coverage preflight, NOT a backtest.

Paste this entire file into a research.ipynb cell. Prints coverage metadata
only. No Alpaca/Neon calls, orders, raw market prices, or scored returns.
Offline tests inject a fake QuantBook; they are NOT real vendor evidence.
"""
from collections import Counter
from datetime import date, datetime
import json

START = datetime(1998, 1, 1)
END_EXCLUSIVE = datetime(2026, 10, 1)
ETFS_ORIGINAL_12 = (
    "SPY", "XLB", "XLC", "XLE", "XLF", "XLI",
    "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
# Amendment 2 is still DRAFT pending approval. Compare both sets without
# changing the frozen executable universe or scored research definitions.
ETFS_PROPOSED_10 = tuple(
    x for x in ETFS_ORIGINAL_12 if x not in ("XLC", "XLRE"))
LIQUID_STOCKS = ("AAPL", "MSFT", "JPM", "XOM", "JNJ", "GE", "INTC")
HISTORIC_DELISTED = ("FRC", "SIVB", "SBNY", "LEH", "WAMUQ", "ENRNQ")
YEARS_TO_TEST = tuple(range(1998, END_EXCLUSIVE.year))
REQUIRED_COMPLETE_YEARS = 10


def historical_dates(frame):
    """Support QC MultiIndex regardless of second-level name, no prices."""
    if frame is None or len(frame) == 0:
        return ()
    index = getattr(frame, "index", None)
    if index is None or not hasattr(index, "get_level_values"):
        raise ValueError("history_index_not_supported")
    days = []
    for timestamp in index.get_level_values(-1):
        if isinstance(timestamp, datetime):
            day = timestamp.date()
        elif isinstance(timestamp, date):
            day = timestamp
        elif hasattr(timestamp, "date"):
            day = timestamp.date()
        else:
            raise ValueError("history_time_not_datetime")
        if not isinstance(day, date):
            raise ValueError("history_time_not_date")
        if START.date() <= day < END_EXCLUSIVE.date():
            days.append(day)
    if len(days) != len(set(days)):
        raise ValueError("duplicate_daily_sessions")
    return tuple(sorted(days))


def date_summary(days):
    counts = Counter(d.year for d in days)
    return {
        "first": days[0].isoformat() if days else None,
        "last": days[-1].isoformat() if days else None,
        "daily_sessions": len(days),
        "year_counts": {str(y): counts[y] for y in sorted(counts)},
        # Oct 2026 is deliberately partial and never a full-year failure.
        "partial_2026_sessions": counts.get(2026, 0),
    }


def _longest_streak(years):
    longest = run = 0
    prev = None
    for year in years:
        run = run + 1 if prev == year - 1 else 1
        longest = max(longest, run)
        prev = year
    return longest


def common_etf_coverage(date_sets, symbols):
    """Necessary 10yr common-session check, NEVER PIT proof."""
    if not isinstance(date_sets, dict) or any(
        ticker not in date_sets or not date_sets[ticker]
        for ticker in symbols
    ):
        return {
            "status": "incomplete_symbols", "full_calendar_years": [],
            "longest_consecutive_years": 0,
            "ten_year_session_coverage": False,
            "point_in_time_survivorship_verified": False,
        }
    spy = set(date_sets["SPY"])
    common = set.intersection(*(set(date_sets[sym]) for sym in symbols))
    spy_counts = Counter(d.year for d in spy)
    common_counts = Counter(d.year for d in common)
    full_years = [
        y for y in YEARS_TO_TEST
        if spy_counts[y] >= 240 and common_counts[y] == spy_counts[y]
    ]
    longest = _longest_streak(full_years)
    return {
        "status": "observed_not_pit_verified",
        "full_calendar_years": full_years,
        "longest_consecutive_years": longest,
        "ten_year_session_coverage": longest >= REQUIRED_COMPLETE_YEARS,
        "point_in_time_survivorship_verified": False,
    }


def _event_count(qb, kind, symbol, start, end):
    try:
        data = qb.history(kind, symbol, start, end)
        return {"status": "ok", "count": 0 if data is None else len(data)}
    except Exception as error:
        # Never print raw exception messages or provider response bodies.
        return {"status": "error", "error_type": type(error).__name__}


def run_coverage(qb, *, resolution, normalization, split, dividend, delisting,
                 emit=print, start=START, end=END_EXCLUSIVE):
    """One symbol at a time, 25 records plus corporate action metadata."""
    if not callable(emit):
        raise ValueError("emit must be callable")
    if start != START or end != END_EXCLUSIVE:
        raise ValueError("frozen_coverage_window_changed")

    def output(kind, data):
        emit("QC_COVERAGE_V2 " + json.dumps(
            {"kind": kind, **data}, sort_keys=True, separators=(",", ":")))

    output("protocol", {
        "from_inclusive": start.date().isoformat(),
        "to_exclusive": end.date().isoformat(),
        "normalization_requested": "TOTAL_RETURN",
        "normalization_independently_verified": False,
        "full_years_only_through": end.year - 1,
        "preregistration_amendment_2_status": "draft_unapproved",
        "raw_market_prices_exported": False,
        "scored_backtest_run": False,
    })
    date_sets = {}
    for group, tickers in (
        ("etf", ETFS_ORIGINAL_12),
        ("stock", LIQUID_STOCKS),
        ("delisted_sample", HISTORIC_DELISTED),
    ):
        for ticker in tickers:
            try:
                # NEVER silently retry in RAW or ADJUSTED mode.
                symbol = qb.add_equity(
                    ticker, resolution,
                    data_normalization_mode=normalization).symbol
            except Exception as error:
                output("symbol", {
                    "group": group, "symbol": ticker,
                    "status": "add_failed",
                    "error_type": type(error).__name__,
                    "normalization": "unverified",
                })
                continue
            try:
                days = historical_dates(qb.history(symbol, start, end, resolution))
            except Exception as error:
                output("symbol", {
                    "group": group, "symbol": ticker,
                    "status": "history_failed",
                    "error_type": type(error).__name__,
                    "normalization": "total_return_requested_unverified",
                })
                continue
            if not days:
                output("symbol", {
                    "group": group, "symbol": ticker,
                    "status": "no_bars",
                    "normalization": "total_return_requested_unverified",
                })
                continue
            if group == "etf":
                date_sets[ticker] = days
            output("symbol", {
                "group": group, "symbol": ticker,
                "status": "bars_present",
                "normalization": "total_return_requested_unverified",
                **date_summary(days),
            })
            if group != "etf":
                for label, kind in (
                    ("splits", split), ("dividends", dividend),
                    ("delistings", delisting),
                ):
                    output("event", {
                        "group": group, "symbol": ticker, "type": label,
                        **_event_count(qb, kind, symbol, start, end),
                    })
    for name, tickers in (
        ("original_12", ETFS_ORIGINAL_12),
        ("proposed_10_unapproved", ETFS_PROPOSED_10),
    ):
        output("common_etf_coverage", {
            "universe": name, "symbols": list(tickers),
            **common_etf_coverage(date_sets, tickers),
        })
    output("limitations", {
        "point_in_time_survivorship_verified": False,
        "historical_membership_source_verified": False,
        "ten_year_strategy_eligible": False,
        "history_coverage_not_equal_to_market_edge": True,
        "no_data_export_or_broker_orders": True,
    })
    output("done", {
        "symbol_records_expected": (
            len(ETFS_ORIGINAL_12) + len(LIQUID_STOCKS)
            + len(HISTORIC_DELISTED)),
    })


if __name__ == "__main__":
    # These globals exist inside the QuantConnect research kernel.
    # A local NameError is NOT historical-data coverage evidence.
    run_coverage(
        QuantBook(), resolution=Resolution.DAILY,
        normalization=DataNormalizationMode.TOTAL_RETURN,
        split=Split, dividend=Dividend, delisting=Delisting,
    )
