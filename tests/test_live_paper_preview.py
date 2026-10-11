"""Read-only live IEX preview integration mocks. No Alpaca orders."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.paper_store import JournalControl
from bot.live_paper_preview_cli import preview_live_quotes


NOW = datetime(2026, 10, 12, 14, 35, tzinfo=timezone.utc)


class FakePaperTrading:
    def __init__(self, opened=True, positions=None):
        self.calls = []
        self.opened = opened
        self.positions = positions or []

    def get_account(self):
        self.calls.append("get_account")
        return SimpleNamespace(equity="500", cash="500",
                               trading_blocked=False)

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(is_open=self.opened)

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return self.positions

    def get_orders(self, *, filter):
        self.calls.append("get_orders")
        return []

    def submit_order(self, **_):
        raise AssertionError("Live quote previews must never submit")


class FakeJournal:
    def __init__(self, kill=True, enabled=False, unresolved=()):
        self.calls = []
        self.kill = kill
        self.enabled = enabled
        self.unresolved = unresolved

    def assert_monitor_only(self):
        self.calls.append("verify_monitor_only")

    def read_control(self):
        self.calls.append("read_control")
        return JournalControl(self.kill, self.enabled)

    def read_unresolved_intents(self, limit):
        self.calls.append("read_unresolved_intents")
        assert limit == 501
        return self.unresolved

    def reserve_intent(self, *_):
        raise AssertionError("Preview must never reserve an intent")


class FakeIEX:
    def __init__(self, age=2):
        self.age = age
        self.calls = []

    def get_stock_latest_quote(self, request):
        self.calls.append(request.symbol_or_symbols)
        return {"F": SimpleNamespace(
            bid_price=25, ask_price=25.02, bid_size=30, ask_size=30,
            timestamp=NOW - timedelta(seconds=self.age)
        )}


def test_market_live_quotes_research_without_submission(monkeypatch):
    monkeypatch.setattr(
        "bot.live_paper_preview_cli.research_candidates",
        lambda *args, **kwargs: [
            {"symbol": "F", "momentum_score": .32,
             "prior_completed_bar_date": "2026-10-09"}
        ],
    )
    trading, journal, quotes = FakePaperTrading(), FakeJournal(), FakeIEX()
    report = preview_live_quotes(
        trading, quotes, journal, symbols=("F",), now=NOW)
    assert report["market_open"] is True
    assert report["data_feed"] == "IEX_ONLY_NOT_CONSOLIDATED_NBBO"
    assert report["broker_writes"] == 0
    assert report["submissions_enabled"] is False
    assert report["kill_switch_active"] is True
    assert report["candidates"][0]["preview"]["shares"] > 0
    assert report["candidates"][0]["submission_enabled"] is False
    assert trading.calls == [
        "get_account", "get_clock", "get_all_positions", "get_orders"
    ]
    assert journal.calls == [
        "verify_monitor_only", "read_control", "read_unresolved_intents"
    ]
    assert quotes.calls == ["F"]


def test_closed_market_doesnt_fetch_latest_quote(monkeypatch):
    monkeypatch.setattr(
        "bot.live_paper_preview_cli.research_candidates",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("No scanner on closed market"))
    )
    report = preview_live_quotes(FakePaperTrading(opened=False),
                                 FakeIEX(), FakeJournal(), now=NOW)
    assert "stock_market_closed" in report["safety_blockers"]
    assert report["candidates"] == []


def test_existing_position_or_pending_journal_prevents_preview(monkeypatch):
    monkeypatch.setattr(
        "bot.live_paper_preview_cli.research_candidates",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Existing exposure must stop all scanner work"))
    )
    report = preview_live_quotes(
        FakePaperTrading(positions=[object()]), FakeIEX(),
        FakeJournal(unresolved=(("hds-a","reserved","F",1),)), now=NOW)
    assert report["candidates"] == []
    assert "account_has_existing_exposure_or_unresolved_orders" in report["safety_blockers"]


def test_db_switch_off_disallows_even_quote_research():
    report = preview_live_quotes(
        FakePaperTrading(), FakeIEX(),
        FakeJournal(kill=False, enabled=True), now=NOW)
    assert report["candidates"] == []
    assert "production_paper_kill_switch_must_remain_engaged" in report["safety_blockers"]


def test_live_stale_quote_is_skipped_not_bought(monkeypatch):
    monkeypatch.setattr(
        "bot.live_paper_preview_cli.research_candidates",
        lambda *_a, **_k: [{"symbol": "F", "momentum_score": .32}],
    )
    report = preview_live_quotes(
        FakePaperTrading(), FakeIEX(age=360),
        FakeJournal(), now=NOW)
    assert "stale" in report["candidates"][0]["blocked_reason"]
    assert report["broker_writes"] == 0


def test_db_outage_never_sends_orders():
    class Down(FakeJournal):
        def assert_monitor_only(self):
            raise ConnectionError("Neon down")

    trading = FakePaperTrading()
    with pytest.raises(ConnectionError):
        preview_live_quotes(trading, FakeIEX(), Down(), now=NOW)
    assert trading.calls == []
