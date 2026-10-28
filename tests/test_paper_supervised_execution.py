"""Offline-only tests for the UNRELEASED Alpaca PAPER submit boundary.

All broker/order/store objects are fakes, never actual network clients.
The module code release switch is OFF except inside explicitly patched
synthetic test cases.
"""
from dataclasses import replace
from contextlib import contextmanager
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
            buying_power=str(self.balance), trading_blocked=False,
        )

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(
            is_open=self.clock_open,
            next_close=datetime(2026, 10, 12, 20, tzinfo=timezone.utc)
        )

    def get_calendar(self, filters):
        self.calls.append("get_calendar")
        if filters.end < NOW.date():
            return [SimpleNamespace(date=NOW.date() - timedelta(days=3))]
        assert filters.start == NOW.date()
        assert filters.end == NOW.date()
        return [SimpleNamespace(
            date=NOW.date(),
            open=datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc),
            close=datetime(2026, 10, 12, 20, tzinfo=timezone.utc),
        )]

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
        if self.fail_record:
            raise RuntimeError("Neon unavailable")

    def record_observed_state(self, cid, status, oid):
        raise AssertionError("Executor must not attest broker status")

    @contextmanager
    def authorize_reserved_submission(self, plan):
        self.mark_uncertain(plan.client_order_id)
        yield


class FakeMarketData:
    def get_stock_bars(self, request):
        days = []
        day = NOW.date() - timedelta(days=3)
        while len(days) < 22:
            if day.weekday() < 5:
                days.append(day)
            day -= timedelta(days=1)
        rows = [SimpleNamespace(
            timestamp=datetime.combine(day, datetime.min.time(), timezone.utc),
            open=20 + i / 10, high=21 + i / 10, low=19 + i / 10,
            close=20 + i / 10, volume=200 if i == 21 else 100)
            for i, day in enumerate(reversed(days))]
        return SimpleNamespace(data={"F": rows})


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
        market_data=FakeMarketData(),
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
    assert result.result == "broker_acknowledged_unreconciled_stop_not_proven"
    assert len(broker.sent) == 1
    order = broker.sent[0]
    assert order.client_order_id == p.client_order_id
    assert order.qty == p.shares
    assert order.limit_price == p.buy_limit_usd
    assert order.stop_loss.stop_price == p.stop_usd
    assert broker.calls.index("get_order_by_client_id") < broker.calls.index("submit_order")
    assert journal.calls[0][0] == "reserve"
    assert journal.calls[1] == ("unknown", p.client_order_id)


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
            preview=p, safety_state=healthy(), approval=None, now=NOW,
            market_data=FakeMarketData())
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


def test_durable_unknown_failure_blocks_before_submit(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal(fail_record=True)
    with pytest.raises(TradeBlocked, match="reauthorization failed before POST"):
        execute(p, broker=broker, journal=journal)
    assert broker.sent == []


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


@pytest.mark.parametrize("broker_clock", ["true", "false", 1, None])
def test_nonboolean_broker_clock_refused_before_reservation(monkeypatch,
                                                            broker_clock):
    # Fake objects only. Never contact Alpaca, never enable the source switch.
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p, clock_open=broker_clock)
    journal = FakeJournal()
    with pytest.raises(TradeBlocked, match="market closed"):
        execute(p, broker=broker, journal=journal)
    assert journal.calls == []
    assert broker.sent == []


@pytest.mark.parametrize("broker_blocked", [None, 0, "false", "true"])
def test_nonboolean_broker_trading_blocked_refused_before_reservation(
        monkeypatch, broker_blocked):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    journal = FakeJournal()
    broker.get_account = lambda: SimpleNamespace(
        equity="500", cash="500", trading_blocked=broker_blocked)
    with pytest.raises(TradeBlocked, match="account blocked"):
        execute(p, broker=broker, journal=journal)
    assert journal.calls == []
    assert broker.sent == []


def test_calendar_holiday_refuses_without_reservation_or_order(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    broker.get_calendar = lambda _filters: []  # holiday, or unavailable session
    with pytest.raises(TradeBlocked, match="session not verified"):
        execute(p, broker=broker, journal=journal)
    assert broker.sent == [] and journal.calls == []


def test_calendar_mismatch_refuses_even_if_clock_claims_open(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    broker.get_calendar = lambda _filters: [SimpleNamespace(
        date=NOW.date(),
        open=datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc),
        close=datetime(2026, 10, 12, 18, tzinfo=timezone.utc),
    )]
    with pytest.raises(TradeBlocked, match="disagree"):
        execute(p, broker=broker, journal=journal)
    assert broker.sent == [] and journal.calls == []


def test_calendar_missing_or_naive_time_refuses(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    broker.get_calendar = lambda _filters: [SimpleNamespace(
        date=NOW.date(), open=datetime(2026, 10, 12, 9, 30),
        close=datetime(2026, 10, 12, 16, 0),
    )]
    with pytest.raises(TradeBlocked, match="timezone-aware"):
        execute(p, broker=broker, journal=journal)
    assert broker.sent == [] and journal.calls == []


def test_calendar_early_close_allows_before_1230_blocks_1230_and_later():
    # Offline synthetic half-day; exact NY local time is controlled.
    from zoneinfo import ZoneInfo
    local = ZoneInfo("America/New_York")
    day = datetime(2026, 11, 27, 12, 30, tzinfo=local).date()
    early_open = datetime(2026, 11, 27, 9, 30, tzinfo=local)
    early_close = datetime(2026, 11, 27, 13, 0, tzinfo=local)
    broker = FakeBroker(preview())
    def calendar(filters):
        assert filters.start == day and filters.end == day
        return [SimpleNamespace(date=day, open=early_open, close=early_close)]
    broker.get_calendar = calendar
    clock = SimpleNamespace(next_close=early_close, is_open=True)
    before = datetime(2026, 11, 27, 12, 29, tzinfo=local)
    at = datetime(2026, 11, 27, 12, 30, tzinfo=local)
    assert entry._verify_paper_market_session(broker, clock, now=before)
    with pytest.raises(TradeBlocked, match="too close"):
        entry._verify_paper_market_session(broker, clock, now=at)


def test_calendar_post_dst_regular_close_verified_in_local_time():
    # NY standard time opens 14:30 UTC and closes 21:00 UTC in November.
    from zoneinfo import ZoneInfo
    local = ZoneInfo("America/New_York")
    day = datetime(2026, 11, 2, 10, 0, tzinfo=local).date()
    opens = datetime(2026, 11, 2, 9, 30, tzinfo=local)
    closes = datetime(2026, 11, 2, 16, 0, tzinfo=local)
    broker = FakeBroker(preview())
    broker.get_calendar = lambda f: [SimpleNamespace(date=day, open=opens, close=closes)]
    assert entry._verify_paper_market_session(
        broker, SimpleNamespace(next_close=closes),
        now=datetime(2026, 11, 2, 10, 0, tzinfo=local))
    with pytest.raises(TradeBlocked, match="too close"):
        entry._verify_paper_market_session(
            broker, SimpleNamespace(next_close=closes),
            now=datetime(2026, 11, 2, 15, 30, tzinfo=local))


def test_calendar_fetch_error_redacts_untrusted_broker_details(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    def broken(_filters):
        raise RuntimeError("SENSITIVE_BROKER_TOKEN")
    broker.get_calendar = broken
    with pytest.raises(TradeBlocked) as exc:
        execute(p, broker=broker, journal=journal)
    assert "SENSITIVE_BROKER_TOKEN" not in str(exc.value)
    assert broker.sent == [] and journal.calls == []


def test_real_alpaca_sdk_calendar_naive_ny_open_close_is_normalized():
    # alpaca-py Calendar constructs naive datetimes from date + HH:MM.
    from alpaca.trading.models import Calendar
    from zoneinfo import ZoneInfo
    local = ZoneInfo("America/New_York")
    session = Calendar(date="2026-11-27", open="09:30", close="13:00")
    assert session.open.tzinfo is None and session.close.tzinfo is None
    broker = FakeBroker(preview())
    broker.get_calendar = lambda _filter: [session]
    clock = SimpleNamespace(
        is_open=True,
        next_close=datetime(2026, 11, 27, 13, 0, tzinfo=local)
    )
    assert entry._verify_paper_market_session(
        broker, clock, now=datetime(2026, 11, 27, 12, 29, tzinfo=local))
    with pytest.raises(TradeBlocked, match="too close"):
        entry._verify_paper_market_session(
            broker, clock, now=datetime(2026, 11, 27, 12, 30, tzinfo=local))


def test_real_alpaca_sdk_calendar_date_must_match_local_market_day():
    from alpaca.trading.models import Calendar
    from zoneinfo import ZoneInfo
    local = ZoneInfo("America/New_York")
    session = Calendar(date="2026-11-27", open="09:30", close="13:00")
    broker = FakeBroker(preview())
    broker.get_calendar = lambda _filter: [session]
    with pytest.raises(TradeBlocked, match="date mismatch"):
        entry._verify_paper_market_session(
            broker, SimpleNamespace(next_close=datetime(
                2026, 11, 30, 16, 0, tzinfo=local)),
            now=datetime(2026, 11, 30, 10, 0, tzinfo=local))


def test_kill_switch_changed_during_reservation_never_posts(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    class KilledJournal(FakeJournal):
        @contextmanager
        def authorize_reserved_submission(self, plan):
            self.mark_uncertain(plan.client_order_id)
            raise TradeBlocked("Kill changed after reservation")
            yield  # pragma: no cover -- context manager protocol
    with pytest.raises(TradeBlocked, match="before POST"):
        execute(p, broker=broker, journal=KilledJournal())
    assert broker.sent == []


def test_lock_wait_cannot_extend_approval_or_quote_lifetime(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    times = iter([100, 221])
    monkeypatch.setattr(entry, "monotonic", lambda: next(times))
    p = preview()
    broker = FakeBroker(p)
    with pytest.raises(TradeBlocked, match="before POST"):
        execute(p, broker=broker)
    assert not broker.sent


def test_account_change_after_reservation_cannot_post(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    class ChangedJournal(FakeJournal):
        @contextmanager
        def authorize_reserved_submission(self, plan):
            self.mark_uncertain(plan.client_order_id)
            broker.balance = 100
            yield
    with pytest.raises(TradeBlocked, match="before POST"):
        execute(p, broker=broker, journal=ChangedJournal())
    assert not broker.sent


def test_missing_buying_power_evidence_cannot_reserve(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker, journal = FakeBroker(p), FakeJournal()
    broker.get_account = lambda: SimpleNamespace(equity="500", cash="500", trading_blocked=False)
    with pytest.raises(TradeBlocked):
        execute(p, broker=broker, journal=journal)
    assert not broker.sent and not journal.calls


@pytest.mark.parametrize("bad_history", ["missing", "stale", "duplicate", "no_signal"])
def test_strategy_must_have_fresh_unique_eligible_completed_bars(monkeypatch, bad_history):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    broker = FakeBroker(p)
    class BadData(FakeMarketData):
        def get_stock_bars(self, request):
            response = super().get_stock_bars(request)
            rows = response.data["F"]
            if bad_history == "missing": response.data["F"] = []
            elif bad_history == "stale": rows.pop()
            elif bad_history == "duplicate": rows.append(rows[-1])
            else: rows[-1].volume = 50
            return response
    with pytest.raises(TradeBlocked, match="momentum signal"):
        entry.submit_paper_once_supervised(
            trading=broker, journal=FakeJournal(), market_quote=quote(),
            preview=p, safety_state=healthy(), approval=approved(p), now=NOW,
            market_data=BadData())
    assert not broker.sent


def test_early_close_deadline_crossed_during_final_reads_blocks(monkeypatch):
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    monkeypatch.setattr(entry, "_verify_paper_market_session", lambda *_a, **_kw: True)
    times = iter([100, 100, 102])
    monkeypatch.setattr(entry, "monotonic", lambda: next(times))
    p = preview()
    broker = FakeBroker(p)
    broker.get_clock = lambda: SimpleNamespace(
        is_open=True, next_close=NOW + timedelta(minutes=30, seconds=1))
    with pytest.raises(TradeBlocked, match="before POST"):
        execute(p, broker=broker)
    assert not broker.sent


def test_simulated_order_to_independent_fill_reconciliation_and_restart(monkeypatch):
    from datetime import date
    from bot.paper_status_reconciler import reconcile_pending_once
    monkeypatch.setattr(entry, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    p = preview()
    class Durable(FakeJournal):
        status = None
        def reserve_if_enabled(self, plan):
            if self.status is not None:
                return False
            self.plan, self.status = plan, "reserved"
            return True
        def mark_uncertain(self, cid):
            assert self.status == "reserved" and cid == self.plan.client_order_id
            self.status = "unknown"
    durable = Durable()
    broker = FakeBroker(p)
    result = execute(p, broker=broker, journal=durable)
    assert result.submitted_to_paper_broker and durable.status == "unknown"
    assert len(broker.sent) == 1
    # New independent process/role reconstructs the intent from durable terms.
    class IndependentJournal:
        def read_pending_plans(self): return [durable.plan]
        def assert_reconciler_permissions(self): pass  # isolated-role fake only
        def get_durable_intent_terms(self, cid):
            plan = durable.plan
            return (durable.status, None, plan.strategy, plan.symbol,
                    date.fromisoformat(plan.trading_day), plan.whole_shares,
                    plan.estimated_entry, plan.protective_stop)
        def record_broker_verified_state(self, cid, old, new, broker_id):
            assert durable.status == old and broker_id == "mock-paper-id"
            durable.status = new
    child = SimpleNamespace(
        id="linked-stop", parent_order_id="mock-paper-id", symbol=p.symbol,
        side="sell", type="stop", status="new", time_in_force="gtc",
        qty=str(p.shares), filled_qty="0", stop_price=str(p.stop_usd))
    nested = SimpleNamespace(
        id="mock-paper-id", client_order_id=p.client_order_id, symbol=p.symbol,
        side="buy", type="limit", order_class="oto", time_in_force="day",
        status="filled", qty=str(p.shares), filled_qty=str(p.shares),
        limit_price=str(p.buy_limit_usd), legs=[child])
    broker.get_order_by_client_id = lambda cid: nested
    broker.get_order_by_id = lambda pid, **kw: nested
    broker.get_all_positions = lambda: [SimpleNamespace(symbol=p.symbol, qty=str(p.shares))]
    states = reconcile_pending_once(trading=broker, journal=IndependentJournal())
    assert states == ((p.client_order_id, "filled"),)
    assert durable.status == "filled" and len(broker.sent) == 1
    # Restart cannot interpret a subsequent broker 404 as resubmission permission.
    broker.get_all_positions = lambda: []
    broker.get_order_by_client_id = FakeBroker(p).get_order_by_client_id
    with pytest.raises(TradeBlocked, match="NEVER retry"):
        execute(p, broker=broker, journal=durable)
    assert len(broker.sent) == 1
