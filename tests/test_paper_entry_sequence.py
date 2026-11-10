"""Up to three daily entries: sequence-aware IDs, unchanged first ID, SQL 004."""
from datetime import date
from pathlib import Path

import pytest

from bot.paper_safety import (
    MAX_ENTRIES_PER_DAY, TradeBlocked, entry_sequence_of, make_client_order_id,
    plan_paper_entry,
)

DAY = date(2026, 11, 10)
ROOT = Path(__file__).resolve().parents[1]


def test_first_entry_id_is_unchanged_from_v1():
    # Live first trade of 2026-11-10 used this exact ID.
    assert make_client_order_id(DAY, "WMT") == "hds-20261110-WMT-e9ac0958d6bb"


def test_each_daily_entry_gets_a_distinct_id_in_the_same_format():
    ids = [make_client_order_id(DAY, "WMT", sequence=n) for n in (1, 2, 3)]
    assert len(set(ids)) == 3
    assert all(i.startswith("hds-20261110-WMT-") and len(i.split("-")[-1]) == 12 for i in ids)
    assert [entry_sequence_of(i, DAY, "WMT") for i in ids] == [1, 2, 3]


@pytest.mark.parametrize("bad", [0, 4, -1, "2", 2.0])
def test_sequence_outside_one_to_three_is_refused(bad):
    with pytest.raises(TradeBlocked):
        make_client_order_id(DAY, "WMT", sequence=bad)


def test_foreign_or_other_day_ids_are_not_daily_entries():
    assert entry_sequence_of("hds-20261110-WMT-000000000000", DAY, "WMT") is None
    assert entry_sequence_of(make_client_order_id(date(2026, 11, 11), "WMT"), DAY, "WMT") is None
    assert MAX_ENTRIES_PER_DAY == 3


def test_journal_validation_accepts_second_entry_and_rejects_forgeries():
    from dataclasses import replace
    from bot.paper_execution_journal import PaperExecutorJournal
    plan = replace(plan_paper_entry("F", DAY, 25, 500, 500, .01), time_in_force="gtc")
    PaperExecutorJournal.validate_protected_intent(plan)
    second = replace(plan, client_order_id=make_client_order_id(DAY, "F", sequence=2))
    PaperExecutorJournal.validate_protected_intent(second)
    with pytest.raises(TradeBlocked, match="Unexpected client order ID"):
        PaperExecutorJournal.validate_protected_intent(
            replace(plan, client_order_id="hds-20261110-F-deadbeefcafe"))


def test_gateway_derives_sequence_from_the_preview_id():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from bot.paper_supervised_execution import _preview_entry_sequence
    p = SimpleNamespace(observed_at_utc=datetime(2026, 11, 10, 16, tzinfo=timezone.utc).isoformat(),
                        symbol="WMT", client_order_id=make_client_order_id(DAY, "WMT", sequence=2))
    assert _preview_entry_sequence(p) == 2
    p.client_order_id = "hds-20261110-WMT-000000000000"
    with pytest.raises(TradeBlocked):
        _preview_entry_sequence(p)


def test_live_preview_builds_the_requested_entry_id():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from bot.live_quotes import preview_live_paper_entry
    now = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)
    quote = SimpleNamespace(bid_price=125.40, ask_price=125.42, bid_size=100, ask_size=100,
                            timestamp=now - timedelta(seconds=1))
    p = preview_live_paper_entry(symbol="WMT", quote=quote, equity=500, available_cash=500,
                                 available_buying_power=500, now=now, market_open=True,
                                 entry_sequence=2)
    assert p.client_order_id == make_client_order_id(DAY, "WMT", sequence=2)


def test_migration_004_raises_cap_to_three_without_touching_other_limits():
    sql = (ROOT / "sql" / "migrations" / "004_entries_per_day_up_to_three.sql").read_text()
    assert "DROP INDEX IF EXISTS public.uq_paper_intents_trading_day;" in sql
    assert "CHECK (max_entries_per_day BETWEEN 1 AND 3)" in sql
    assert "SET max_entries_per_day = 3" in sql and "'policy_changed'" in sql
    for untouched in ("max_trade_risk_fraction", "max_position_fraction", "paper_control",
                      "GRANT", "CREATE OR REPLACE FUNCTION", "autonomous_entries_enabled"):
        assert untouched not in sql
