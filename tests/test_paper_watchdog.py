"""Read-only Neon/Alpaca preflight tests without external API calls."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.paper_store import JournalControl
from bot.paper_watchdog import new_york_regular_open_utc, read_only_tick


class FakeJournal:
    def __init__(self, *, intents=(), start=500, peak=500,
                 kill_switch=True, submissions_enabled=False):
        self.calls = []
        self.intents = intents
        self.start = start
        self.peak = peak
        self.kill_switch = kill_switch
        self.submissions_enabled = submissions_enabled

    def assert_monitor_only(self):
        self.calls.append("assert_monitor_only")

    def read_control(self):
        self.calls.append("read_control")
        return JournalControl(self.kill_switch, self.submissions_enabled)

    def read_unresolved_intents(self, limit):
        self.calls.append("read_unresolved_intents")
        assert limit == 501
        return list(self.intents)

    def read_equity_references(self, start_utc, as_of_utc):
        self.calls.append("read_equity_references")
        assert start_utc.tzinfo is not None
        assert as_of_utc.tzinfo is not None
        return self.start, self.peak

    def reserve_intent(self, *_):
        raise AssertionError("Preflight must NEVER reserve an intent")

    def record_equity(self, *_):
        raise AssertionError("Preflight must NEVER write equity")


class FakeBroker:
    def __init__(self, *, equity=500, cash=500, positions=(),
                 open_orders=(), closed_orders=(), clock_open=True):
        self.calls = []
        self.equity = equity
        self.cash = cash
        self.positions = list(positions)
        self.open_orders = list(open_orders)
        self.closed_orders = list(closed_orders)
        self.clock_open = clock_open

    def get_account(self):
        self.calls.append("get_account")
        return SimpleNamespace(equity=str(self.equity), cash=str(self.cash),
                               trading_blocked=False)

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(is_open=self.clock_open)

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return self.positions

    def get_orders(self, *, filter):
        self.calls.append("get_orders_" + str(filter.status.value))
        assert filter.nested is True and filter.limit == 500
        if str(filter.status.value) == "open":
            return self.open_orders
        if str(filter.status.value) == "closed":
            assert filter.after is not None
            return self.closed_orders
        raise AssertionError("Unexpected broker request")

    def submit_order(self, *_args, **_kwargs):
        raise AssertionError("NO broker order may be submitted")

    def cancel_order_by_id(self, *_args, **_kwargs):
        raise AssertionError("NO broker order may be canceled")

    def close_all_positions(self, *_args, **_kwargs):
        raise AssertionError("NO broker position may be closed")


def test_paper_preflight_never_trades_even_with_open_switches():
    broker = FakeBroker()
    journal = FakeJournal(kill_switch=False, submissions_enabled=True)
    now = datetime(2026, 10, 12, 14, 40, tzinfo=timezone.utc)
    report = read_only_tick(broker, journal, now=now)
    assert broker.calls == [
        "get_account", "get_clock", "get_all_positions",
        "get_orders_open", "get_orders_closed",
    ]
    assert journal.calls == [
        "assert_monitor_only", "read_control", "read_unresolved_intents",
        "read_equity_references",
    ]
    assert report["reconciliation"]["consistent"] is True
    assert report["can_submit_orders"] is False
    assert report["code_submissions_enabled"] is False
    assert report["broker_write_operations"] == 0
    assert "paper_order_submission_disabled" in report["safety_blockers"]
    assert "preflight_has_no_broker_write_capability" in report["safety_blockers"]


def test_kill_switch_and_missing_equity_references_both_block():
    report = read_only_tick(
        FakeBroker(), FakeJournal(start=None, peak=None))
    assert report["journal_kill_switch_active"] is True
    assert "kill_switch_active" in report["safety_blockers"]
    assert "missing_daily_open_equity" in report["safety_blockers"]
    assert "missing_durable_equity_peak" in report["safety_blockers"]


def test_db_connection_outage_aborts_without_broker_writes():
    class BrokenJournal(FakeJournal):
        def read_control(self):
            raise ConnectionError("Neon offline")

    broker = FakeBroker()
    with pytest.raises(ConnectionError):
        read_only_tick(broker, BrokenJournal())
    assert broker.calls == []


def test_unresolved_intents_stop_new_trading_even_without_broker_orders():
    report = read_only_tick(
        FakeBroker(), FakeJournal(intents=[("hds-old", "unknown", "F", 1)]))
    assert "unresolved_order_intent_requires_broker_lookup" in report["safety_blockers"]
    assert not report["reconciliation"]["consistent"]


def test_full_500_open_orders_fail_closed():
    orders = [
        SimpleNamespace(id=f"id-{i}", status="new", symbol="F", qty="1",
                        side="buy", type="limit", client_order_id="stranger",
                        legs=[], parent_order_id=None)
        for i in range(500)
    ]
    report = read_only_tick(
        FakeBroker(open_orders=orders), FakeJournal())
    assert "broker_order_history_incomplete" in report["safety_blockers"]


def test_too_many_unresolved_db_intents_aborts():
    intents = [("hds-" + str(i), "reserved", "F", 1) for i in range(501)]
    broker = FakeBroker()
    with pytest.raises(RuntimeError):
        read_only_tick(broker, FakeJournal(intents=intents))
    assert broker.calls == []


def test_new_york_open_handles_daylight_saving_time():
    july = datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc)
    january = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
    assert new_york_regular_open_utc(july).hour == 13
    assert new_york_regular_open_utc(january).hour == 14
    assert new_york_regular_open_utc(july).minute == 30
    with pytest.raises(ValueError):
        new_york_regular_open_utc(datetime(2026, 1, 5))


def test_admin_credential_rejected_before_any_broker_call():
    from bot.paper_safety import TradeBlocked

    class AdminJournal(FakeJournal):
        def assert_monitor_only(self):
            raise TradeBlocked("Neon connection inherited admin permissions")

    broker = FakeBroker()
    with pytest.raises(TradeBlocked, match="admin"):
        read_only_tick(broker, AdminJournal())
    assert broker.calls == []
