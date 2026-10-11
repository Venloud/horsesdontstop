"""Offline-only tests for the UNRELEASED Alpaca PAPER submit boundary.

All broker/order/store objects are fakes, never actual network clients.
The module code release switch is OFF except inside explicitly patched
synthetic test cases.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.live_quotes import preview_live_paper_entry
from bot.paper_safety import (
    PAPER_ENDPOINT, PaperSafetyState, TradeBlocked,
)
from bot import paper_supervised_execution as entry


NOW = datetime(2026, 10, 12, 14, 45, tzinfo=timezone.utc)


def quote(age=2):
    return SimpleNamespace(
        bid_price=25, ask_price=25.02,
        bid_size=50, ask_size=50,
        timestamp=NOW - timedelta(seconds=age),
    )


def preview(quote_=None):
    return preview_live_paper_entry(
        symbol="F", quote=quote_ or quote(),
        equity=500, available_cash=500,
        now=NOW, market_open=True,
    )


def approved(p):
    return entry.ExplicitPaperTradeApproval(
        client_order_id=p.client_order_id,
        symbol=p.symbol,
        shares=p.shares,
        max_buy_limit_usd=p.buy_limit_usd,
        protective_stop_usd=p.stop_usd,
        approved_at_utc=NOW - timedelta(seconds=15),
        expires_at_utc=NOW + timedelta(seconds=45),
    )


def healthy(**updates):
    args = dict(
        equity=500, available_cash=500,
        day_open_equity=500, recorded_peak_equity=500,
        open_positions=0, open_orders=0, market_open=True,
        trading_blocked=False, recent_quote_seconds=2,
        durable_journal_healthy=True, reconciled_with_broker=True,
        watchdog_heartbeat_seconds=2,
        kill_switch_active=False,
        order_submissions_explicitly_enabled=True,
        broker_endpoint=PAPER_ENDPOINT,
        audited_external_flows_complete=True,
        verified_equity_history_complete=True,
        exchange_calendar_verified=True,
    )
    args.update(updates)
    return PaperSafetyState(**args)


class FakeBroker:
    def __init__(self, p, *, existing=False, failure=None,
                 clock_open=True, balance=500, positions=()):
        self.p = p
        self._base_url = PAPER_ENDPOINT
        self.calls = []
        self.existing = existing
        self.failure = failure
        self.clock_open = clock_open
        self.balance = balance
        self.positions = positions
        self.sent = []

    def get_account(self):
        self.calls.append("get_account")
        return SimpleNamespace(
            equity=str(self.balance), cash=str(self.balance),
            trading_blocked=False,
        )

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(is_open=self.clock_open)

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return list(self.positions)

    def get_orders(self, *, filter):
        self.calls.append("get_orders")
        assert filter.nested is True
        return []

    def get_order_by_client_id(self, cid):
        self.calls.append("get_order_by_client_id")
        assert cid == self.p.client_order_id
        if self.existing:
            return SimpleNamespace(id="existing", client_order_id=cid)
        fake_response = SimpleNamespace(response=SimpleNamespace(status_code=404))
        raise APIError('{"message":"not found"}', http_error=fake_response)

    def submit_order(self, *, order_data):
        self.calls.append("submit_order")
        self.sent.append(order_data)
        if self.failure:
            raise self.failure
        return SimpleNamespace(
            id="mock-paper-id", client_order_id=order_data.client_order_id,
            side="buy",
        )


class FakeJournal:
    def __init__(self, *, reservation=True, fail_record=False):
        self.calls = []
        self.reservation = reservation
        self.fail_record = fail_record

    def reserve_if_enabled(self, plan):
        self.calls.append(("reserve", plan))
        return self.reservation

    def mark_uncertain(self, cid):
        self.calls.append(("unknown", cid))

    def record_observed_state(self, cid, status, oid):
        self.calls.append(("observe", cid, status, oid))
        if self.fail_record:
            raise RuntimeError("Neon unavailable")


def execute(p=None, *, broker=None, journal=None, state=None,
            approval=None, live_quote=None, now=NOW):
    p = p or preview()
    broker = broker or FakeBroker(p)
    journal = journal or FakeJournal()
    live_quote = live_quote or quote()
    return entry.submit_paper_once_supervised(
        trading=broker, journal=journal, market_quote=live_quote,
        preview=p, safety_state=state or healthy(),
        approval=approval or approved(p), now=now,
    )


def test_release_is_off_by_default_and_blocks_before_any_network():
    assert entry.PAPER_ORDER_SUBMISSIONS_RELEASED is False
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    with pytest.raises(TradeBlocked, match="NOT RELEASED"):
        execute(p, broker=broker, journal=journal)
    assert broker.calls == []
    assert journal.calls == []


def test_mock_only_happy_path_one_protected_paper_order(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    result = execute(p, broker=broker, journal=journal)
    assert result.client_order_id == p.client_order_id
    assert result.broker_order_id == "mock-paper-id"
    assert result.result == "submitted_unreconciled_stop_not_proven"
    assert len(broker.sent) == 1
    order = broker.sent[0]
    assert order.client_order_id == p.client_order_id
    assert order.qty == p.shares
    assert order.limit_price == p.buy_limit_usd
    assert order.stop_loss.stop_price == p.stop_usd
    assert broker.calls.index("get_order_by_client_id") < broker.calls.index("submit_order")
    assert journal.calls[0][0] == "reserve"
    assert journal.calls[1] == ("observe", p.client_order_id,
                                "submitted", "mock-paper-id")


def test_fake_timeout_marked_unknown_never_retried(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, failure=TimeoutError("paper broker timed out"))
    journal = FakeJournal()
    with pytest.raises(TradeBlocked, match="outcome UNKNOWN"):
        execute(p, broker=broker, journal=journal)
    assert len(broker.sent) == 1
    assert journal.calls[0][0] == "reserve"
    assert journal.calls[-1] == ("unknown", p.client_order_id)
    # One lookup before submission, one additional lookup after timeout.
    assert broker.calls.count("get_order_by_client_id") == 2


def test_existing_broker_order_id_stops_before_db_reservation(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, existing=True)
    journal = FakeJournal()
    with pytest.raises(TradeBlocked, match="already has"):
        execute(p, broker=broker, journal=journal)
    assert not broker.sent
    assert journal.calls == []


def test_journal_unique_conflict_prevents_second_broker_submit(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    journal = FakeJournal(reservation=False)
    with pytest.raises(TradeBlocked, match="NEVER retry"):
        execute(p, broker=broker, journal=journal)
    assert broker.sent == []
    assert journal.calls[0][0] == "reserve"


def test_missing_approval_stops_entry(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    with pytest.raises(TradeBlocked, match="approval"):
        entry.submit_paper_once_supervised(
            trading=broker, journal=journal, market_quote=quote(),
            preview=p, safety_state=healthy(), approval=None, now=NOW)
    assert journal.calls == []
    assert not broker.sent


@pytest.mark.parametrize("field,value", [
    ("shares", 99), ("symbol", "NVDA"), ("max_buy_limit_usd", 100),
    ("protective_stop_usd", 0),
])
def test_approval_must_match_exact_paper_trade(monkeypatch, field, value):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    with pytest.raises(TradeBlocked, match="Approval does not match"):
        execute(p, broker=broker, journal=journal,
                approval=replace(approved(p), **{field: value}))
    assert not broker.sent


def test_expired_approval_cannot_send_an_order(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    approval = replace(
        approved(p),
        approved_at_utc=NOW - timedelta(minutes=3),
        expires_at_utc=NOW - timedelta(minutes=1),
    )
    broker = FakeBroker(p)
    with pytest.raises(TradeBlocked, match="approval"):
        execute(p, broker=broker, approval=approval)
    assert not broker.sent


@pytest.mark.parametrize("blocked", [
    {"kill_switch_active": True},
    {"order_submissions_explicitly_enabled": False},
    {"durable_journal_healthy": False},
    {"watchdog_heartbeat_seconds": None},
    {"day_open_equity": None},
    {"recorded_peak_equity": None},
    {"market_open": False},
    {"broker_endpoint": "https://api.alpaca.markets"},
])
def test_independent_risk_gate_blocks_even_after_mock_release(monkeypatch, blocked):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    with pytest.raises(TradeBlocked):
        execute(p, broker=broker, state=healthy(**blocked))
    assert broker.calls == []


@pytest.mark.parametrize("condition", [
    {"clock_open": False},
    {"balance": 450},
    {"positions": [object()]},
])
def test_updated_paper_account_blocks_pre_submission(monkeypatch, condition):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, **condition)
    journal = FakeJournal()
    with pytest.raises(TradeBlocked):
        execute(p, broker=broker, journal=journal)
    assert journal.calls == []
    assert broker.sent == []


def test_quoted_buy_limit_change_blocks_prior_approval(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    live = SimpleNamespace(
        timestamp=NOW - timedelta(seconds=1),
        bid_price=25, ask_price=25.15, bid_size=50, ask_size=50)
    with pytest.raises(TradeBlocked, match="changed"):
        execute(p, broker=broker, live_quote=live)
    assert broker.sent == []


def test_journal_update_failure_after_submit_never_triggers_retry(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal(fail_record=True)
    with pytest.raises(TradeBlocked, match="durable journal update failed"):
        execute(p, broker=broker, journal=journal)
    assert len(broker.sent) == 1


def test_untrusted_broker_exception_not_exposed(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, failure=RuntimeError("NEON_PRIVATE_PASSWORD"))
    with pytest.raises(TradeBlocked) as error:
        execute(p, broker=broker)
    assert "NEON_PRIVATE_PASSWORD" not in str(error.value)


def test_ambiguous_accepted_order_lookup_never_submits_twice(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, failure=TimeoutError("Accepted but response lost"))
    calls = {"n": 0}
    original = broker.get_order_by_client_id

    def get_by_id(cid):
        calls["n"] += 1
        if calls["n"] == 1:
            return original(cid)  # first lookup 404 before DB reservation
        return SimpleNamespace(id="broker-created", client_order_id=cid)

    broker.get_order_by_client_id = get_by_id
    broker.get_order_by_id = lambda _id, *, filter: SimpleNamespace(
        id="broker-created", client_order_id=p.client_order_id,
        symbol=p.symbol, order_class="oto", side="buy",
        type="limit", time_in_force="day", status="new",
        qty=str(p.shares), filled_qty="0",
        limit_price=str(p.buy_limit_usd), legs=[]
    )
    journal = FakeJournal()
    with pytest.raises(TradeBlocked, match="client-ID lookup"):
        execute(p, broker=broker, journal=journal)
    assert calls["n"] == 2
    assert len(broker.sent) == 1
    assert journal.calls[-1] == ("unknown", p.client_order_id)


def test_actual_nonpaper_broker_host_rejected_before_get(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    broker._base_url = "https://api.alpaca.markets"
    with pytest.raises(TradeBlocked, match="Actual broker"):
        execute(p, broker=broker)
    assert broker.calls == []
    assert broker.sent == []


@pytest.mark.parametrize("blocked", [
    {"audited_external_flows_complete": False},
    {"verified_equity_history_complete": False},
    {"exchange_calendar_verified": False},
])
def test_synthetic_broker_release_rejects_unverified_risk_evidence(monkeypatch, blocked):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    with pytest.raises(TradeBlocked):
        execute(p, broker=broker, state=healthy(**blocked))
    assert broker.calls == []
    assert broker.sent == []
