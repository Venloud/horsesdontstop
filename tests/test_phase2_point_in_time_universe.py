"""Synthetic fixtures only; zero provider data or brokerage API calls."""
from datetime import datetime, timezone, timedelta

import pytest

from bot.phase2_point_in_time_universe import (
    UniverseBlocked, parse_snapshot_csv, read_snapshot_csv,
    select_historical_universe,
)


DECISION = datetime(2023, 5, 3, 13, 30, tzinfo=timezone.utc)
SNAP_DAY = "2023-05-02"
AS_KNOWN = "2023-05-02T20:10:00Z"


def row(sid="QC:SID:FRC", ticker="FRC", eligible="true", **changes):
    record = {
        "snapshot_day": SNAP_DAY,
        "known_at_utc": AS_KNOWN,
        "snapshot_id": "session-20230502-v1",
        "security_id": sid,
        "symbol": ticker,
        "eligible": eligible,
        "source_id": "point-in-time-synthetic-fixture",
        "complete": "true",
    }
    record.update(changes)
    return record


def select(rows, day=SNAP_DAY, decision=DECISION):
    return select_historical_universe(
        rows, snapshot_day=day, decision_at_utc=decision)


def test_only_stable_security_ids_from_complete_prior_day_are_selected():
    q = select([row(), row("QC:SID:AAPL", "AAPL"),
                row("QC:SID:DELISTED", "OLD", eligible="false")])
    assert q.snapshot_day == "2023-05-02"
    assert q.eligible_security_ids == ("QC:SID:AAPL", "QC:SID:FRC")
    assert q.eligible_symbols == ("AAPL", "FRC")
    assert q.known_at_utc == "2023-05-02T20:10:00+00:00"
    assert "QC:SID:DELISTED" not in q.eligible_security_ids
    assert q.source_id == "point-in-time-synthetic-fixture"


def test_ticker_renames_keep_same_stable_security_identity():
    history = [
        row("QC:ID:XYZ", "OLD"),
        row("QC:ID:XYZ", "NEW",
            snapshot_day="2023-05-03",
            snapshot_id="session-20230503-v1",
            known_at_utc="2023-05-03T20:10:00Z"),
    ]
    yesterday = select(history)
    tomorrow = select(history, day="2023-05-03",
                      decision=datetime(2023, 5, 4, 13, 30,
                                        tzinfo=timezone.utc))
    assert yesterday.eligible_security_ids == tomorrow.eligible_security_ids
    assert yesterday.eligible_symbols == ("OLD",)
    assert tomorrow.eligible_symbols == ("NEW",)


def test_delisting_event_can_remove_from_NEXT_day_not_yesterday():
    history = [
        row(),
        row(eligible="false",
            snapshot_day="2023-05-03",
            snapshot_id="session-20230503-v1",
            known_at_utc="2023-05-03T20:10:00Z"),
    ]
    before = select(history)
    after = select(history, day="2023-05-03",
                   decision=datetime(2023, 5, 4, 13, 30,
                                     tzinfo=timezone.utc))
    assert before.eligible_security_ids == ("QC:SID:FRC",)
    assert after.eligible_security_ids == ()


def test_future_delisting_facts_do_not_affect_previous_selection():
    past = select([row(), row(
        eligible="false", snapshot_day="2023-05-03",
        known_at_utc="2023-05-03T20:10:00Z",
        snapshot_id="future-event")])
    assert past.eligible_symbols == ("FRC",)


def test_future_known_timestamp_fails_closed_instead_of_filtering():
    with pytest.raises(UniverseBlocked, match="future"):
        select([row(known_at_utc="2023-05-03T15:00:00Z")])


def test_missing_snapshot_does_not_use_current_ticker_list():
    with pytest.raises(UniverseBlocked, match="absent"):
        select([row(snapshot_day="2023-05-01")])


@pytest.mark.parametrize("bad", [
    {"complete": "false"},
    {"complete": "TRUE"},
    {"complete": ""},
    {"known_at_utc": "2023-05-03T14:00:00Z"},
    {"known_at_utc": "2023-05-02T20:10:00"},
    {"known_at_utc": "garbage"},
    {"eligible": "unknown"},
    {"eligible": "TRUE"},
    {"eligible": ""},
    {"security_id": ""},
    {"security_id": "BAD ID"},
    {"source_id": ""},
    {"snapshot_id": ""},
    {"symbol": "future stock"},
    {"symbol": "aapl"},
    {"symbol": "<script>"},
    {"snapshot_day": "2023-05-40"},
])
def test_malformed_or_unprovable_snapshot_is_blocked(bad):
    with pytest.raises(UniverseBlocked):
        select([row(**bad)])


def test_multiple_snapshot_versions_same_session_fail_closed():
    rows = [
        row(),
        row("QC:SID:OTHER", "OTHER", snapshot_id="version2")
    ]
    with pytest.raises(UniverseBlocked, match="Conflicting or multiple"):
        select(rows)


def test_conflicting_known_at_cannot_merge_asof_slices():
    with pytest.raises(UniverseBlocked, match="Conflicting"):
        select([
            row(),
            row("QC:SID:OTHER", "OTHER",
                known_at_utc="2023-05-02T21:00:00Z"),
        ])


def test_duplicate_stable_security_id_cannot_enter_twice():
    with pytest.raises(UniverseBlocked, match="Duplicate historical security ID"):
        select([row(), row(ticker="FRCA")])


def test_same_ticker_mapped_to_two_historical_ids_blocks():
    with pytest.raises(UniverseBlocked, match="Ambiguous historical ticker"):
        select([row(), row("QC:OTHER:SID", "FRC")])


@pytest.mark.parametrize("day,decision", [
    ("2023-05-03", DECISION),
    ("2023-05-04", DECISION),
    ("2023-05-02", datetime(2023, 5, 2, 14, 0, tzinfo=timezone.utc)),
    ("2023-05-02", datetime(2023, 5, 3, 9, 0)),  # naive
])
def test_same_day_future_snapshot_or_naive_decision_rejected(day, decision):
    with pytest.raises(UniverseBlocked):
        select([row(snapshot_day=day)], day=day, decision=decision)


def test_snapshot_older_than_four_calendar_days_is_rejected():
    decision = datetime(2023, 5, 9, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(UniverseBlocked, match="too stale"):
        select([row()], decision=decision)


def test_weekend_gap_is_accepted_if_snapshot_is_complete_and_known():
    fri = row(snapshot_day="2023-05-05",
              snapshot_id="friday-snapshot",
              known_at_utc="2023-05-05T20:10:00Z")
    monday = datetime(2023, 5, 8, 13, 30, tzinfo=timezone.utc)
    q = select([fri], day="2023-05-05", decision=monday)
    assert q.eligible_security_ids == ("QC:SID:FRC",)


def test_csv_parser_requires_exact_point_in_time_fields():
    old_format = (
        "symbol,first_eligible_day,last_eligible_day\n"
        "FRC,2010-01-01,2023-05-03\n"
    )
    with pytest.raises(UniverseBlocked, match="point-in-time fields"):
        parse_snapshot_csv(old_format)


def test_csv_rejects_duplicate_columns_in_header():
    fields = (
        "snapshot_day,known_at_utc,snapshot_id,security_id,symbol,"
        "eligible,source_id,complete,eligible\n"
    )
    with pytest.raises(UniverseBlocked, match="point-in-time fields"):
        parse_snapshot_csv(fields + "2023-05-02,2023-05-02T20:10Z,s1,sid,FRC,true,src,true,true\n")


def test_csv_round_trip_offline(tmp_path):
    file = tmp_path / "snapshots.csv"
    file.write_text(
        "snapshot_day,known_at_utc,snapshot_id,security_id,symbol,eligible,source_id,complete\n"
        "2023-05-02,2023-05-02T20:10:00Z,s1,QC:FRC,FRC,true,test,true\n"
        "2023-05-02,2023-05-02T20:10:00Z,s1,QC:AAPL,AAPL,true,test,true\n"
    )
    q = select(read_snapshot_csv(file))
    assert q.eligible_security_ids == ("QC:AAPL", "QC:FRC")


def test_csv_missing_rows_and_extra_columns_abort():
    header = (
        "snapshot_day,known_at_utc,snapshot_id,security_id,symbol,"
        "eligible,source_id,complete\n"
    )
    with pytest.raises(UniverseBlocked, match="No historical universe snapshots"):
        parse_snapshot_csv(header)
    with pytest.raises(UniverseBlocked, match="Malformed"):
        parse_snapshot_csv(header + "2023-05-02,2023-05-02T20:10:00Z,s1,QC:FRC,FRC,true,src,true,UNEXPECTED\n")


def test_naive_snapshot_time_is_not_trusted():
    with pytest.raises(UniverseBlocked, match="Timezone-less"):
        select([row(known_at_utc="2023-05-02T20:10:00")])


def test_offline_selector_contains_no_broker_or_external_network_use():
    from pathlib import Path
    import bot.phase2_point_in_time_universe as module
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "import alpaca" not in src
    assert "TradingClient(" not in src
    assert ".submit_order(" not in src
    assert "import requests" not in src
    assert "urlopen(" not in src
    assert ".connect(" not in src
