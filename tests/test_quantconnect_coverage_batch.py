"""Offline fake-QuantBook coverage tests, no cloud/provider/API calls."""
from datetime import date, datetime, timedelta
import json

import pytest

from research.quantconnect.phase2_coverage_batch import (
    START, END_EXCLUSIVE, ETFS_ORIGINAL_12, YEARS_TO_TEST,
    common_etf_coverage, historical_dates, run_coverage, date_summary,
)


def days(first, last):
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    return tuple(a + timedelta(days=i) for i in range((b-a).days)
                 if (a + timedelta(days=i)).weekday() < 5)


class FakeFrame:
    def __init__(self, dates):
        self.dates = dates
        self.index = self
    def __len__(self):
        return len(self.dates)
    def get_level_values(self, key):
        assert key == -1
        return self.dates


class Symbol:
    def __init__(self, text):
        self.ticker = text


class FakeSubscription:
    def __init__(self, text):
        self.symbol = Symbol(text)


class FakeQuantBook:
    def __init__(self, calendars=None, cannot_add=()):
        self.calendars = calendars or {}
        self.cannot_add = cannot_add
        self.calls = []
    def add_equity(self, ticker, resolution, data_normalization_mode=None):
        self.calls.append(("add", ticker, data_normalization_mode))
        assert resolution == "DAILY"
        assert data_normalization_mode == "TOTAL_RETURN"
        if ticker in self.cannot_add:
            raise RuntimeError("SECRET_PROVIDER_EXCEPTION_TEXT")
        return FakeSubscription(ticker)
    def history(self, symbol, start, end, resolution=None):
        if isinstance(symbol, str):
            self.calls.append(("event", symbol))
            return [object(), object()]
        self.calls.append(("bars", symbol.ticker))
        dates = self.calendars.get(symbol.ticker, ())
        return FakeFrame([datetime.combine(d, datetime.min.time())
                          for d in dates])


def probe(qb):
    out = []
    run_coverage(
        qb, resolution="DAILY", normalization="TOTAL_RETURN",
        split="Split", dividend="Dividend", delisting="Delisting",
        emit=out.append)
    assert all(x.startswith("QC_COVERAGE_V2 ") for x in out)
    return [json.loads(x.removeprefix("QC_COVERAGE_V2 ")) for x in out]


def test_original_12_cannot_fake_10yr_but_proposed_10_has_synthetic_overlap():
    full = days("2012-01-01", "2026-01-01")
    calendars = {sym: full for sym in ETFS_ORIGINAL_12}
    calendars["XLRE"] = tuple(x for x in full if x.year >= 2016)
    calendars["XLC"] = tuple(x for x in full if x.year >= 2019)
    qb = FakeQuantBook(calendars)
    data = probe(qb)
    by_name = {x["universe"]: x for x in data
               if x["kind"] == "common_etf_coverage"}
    assert by_name["original_12"]["longest_consecutive_years"] == 7
    assert by_name["original_12"]["ten_year_session_coverage"] is False
    assert by_name["proposed_10_unapproved"]["longest_consecutive_years"] == 14
    assert by_name["proposed_10_unapproved"]["ten_year_session_coverage"] is True
    assert by_name["proposed_10_unapproved"]["point_in_time_survivorship_verified"] is False
    assert len([x for x in data if x["kind"] == "symbol"]) == 25
    assert len([x for x in qb.calls if x[0] == "add"]) == 25
    assert data[-2]["kind"] == "limitations"
    assert data[-2]["ten_year_strategy_eligible"] is False


def test_failed_total_return_subscription_never_retries_unadjusted():
    qb = FakeQuantBook(cannot_add=("SPY",))
    rows = probe(qb)
    spy = next(x for x in rows if x.get("symbol") == "SPY")
    assert spy["status"] == "add_failed"
    assert spy["normalization"] == "unverified"
    assert spy["error_type"] == "RuntimeError"
    assert qb.calls.count(("add", "SPY", "TOTAL_RETURN")) == 1
    assert "SECRET_PROVIDER_EXCEPTION_TEXT" not in str(rows)
    comparison = next(x for x in rows if x.get("universe") == "original_12")
    assert comparison["status"] == "incomplete_symbols"


def test_one_etf_missing_session_invalidates_entire_year():
    full = days("2012-01-01", "2026-01-01")
    calendars = {sym: full for sym in ETFS_ORIGINAL_12}
    calendars["XLK"] = tuple(x for x in full if x != date(2018, 5, 15))
    result = common_etf_coverage(calendars, ETFS_ORIGINAL_12)
    assert 2018 not in result["full_calendar_years"]
    assert result["longest_consecutive_years"] == 7


def test_duplicate_or_unparseable_history_timestamp_refused():
    with pytest.raises(ValueError, match="duplicate"):
        historical_dates(FakeFrame([datetime(2020, 1, 1)] * 2))
    with pytest.raises(ValueError, match="datetime"):
        historical_dates(FakeFrame(["2020-01-01"]))


def test_missing_symbol_series_cannot_claim_10year_coverage():
    data = probe(FakeQuantBook())
    assert {x["status"] for x in data if x["kind"] == "symbol"} == {"no_bars"}
    assert all(not x["ten_year_session_coverage"] for x in data
               if x["kind"] == "common_etf_coverage")


def test_partial_2026_does_not_count_as_missing_complete_calendar_year():
    dates = days("2026-01-01", "2026-10-01")
    assert date_summary(dates)["partial_2026_sessions"] == len(dates)
    assert 2026 not in YEARS_TO_TEST


def test_window_is_pinned_so_comparisons_are_reproducible():
    with pytest.raises(ValueError, match="frozen_coverage_window_changed"):
        run_coverage(
            FakeQuantBook(), resolution="DAILY",
            normalization="TOTAL_RETURN",
            split="Split", dividend="Dividend", delisting="Delisting",
            start=START, end=datetime(2026, 10, 15))


def test_importing_coverage_module_does_not_run_quantbook():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "research/quantconnect/phase2_coverage_batch.py").read_text()
    for name in ("import alpaca", "import psycopg", "submit_order(",
                 "TradingClient(", ".to_csv(", ".to_parquet("):
        assert name not in source
    assert END_EXCLUSIVE == datetime(2026, 10, 1)
