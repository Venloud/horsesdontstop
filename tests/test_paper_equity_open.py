"""Opening PAPER equity: mock only, zero live brokerage orders."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot.paper_equity_open import opening_window, snapshot_one_opening
from bot.paper_execution_journal import PaperExecutorJournal
from bot.paper_safety import TradeBlocked


# Monday October 12, 2026, NY is EDT (UTC-4).
NOW = datetime(2026, 10, 12, 13, 31, tzinfo=timezone.utc)
OPEN = datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc)


class FakeClock:
    is_open = True


class Broker:
    def __init__(self, *, equity="500", cash="500", open=True):
        self.calls = []
        self.equity, self.cash, self.open = equity, cash, open

    def get_clock(self):
        self.calls.append("clock")
        return SimpleNamespace(is_open=self.open)

    def get_account(self):
        self.calls.append("account")
        return SimpleNamespace(
            equity=self.equity, cash=self.cash, trading_blocked=False)

    def submit_order(self, *_a, **_kw):
        raise AssertionError("An equity snapshot may NEVER submit a trade")


class Cursor:
    def __init__(self, db):
        self.db = db
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.db["sql"].append((sql, params))
        if sql.startswith("SET LOCAL"):
            self.row = None
        elif "pg_advisory_xact_lock" in sql:
            self.row = (None,)
        elif "SELECT clock_timestamp()" in sql:
            self.row = (self.db["db_now"], *self.db["switches"])
        elif "SELECT snapshot_id" in sql:
            self.row = (1,) if self.db["snapshots"] else None
        elif "INSERT INTO public.paper_equity_snapshots" in sql:
            self.db["snapshots"].append(params)
            self.row = (len(self.db["snapshots"]),)
        else:
            raise AssertionError("Unexpected SQL: " + sql)

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return Cursor(self.db)


def journal(monkeypatch, *, db_now=NOW, switches=(True, False)):
    db = {"db_now": db_now, "switches": switches, "snapshots": [],
          "sql": [], "checked_role": 0}
    j = PaperExecutorJournal("postgresql://invalid.neon/test")
    def check_role():
        db["checked_role"] += 1
    monkeypatch.setattr(j, "assert_executor_permissions", check_role)
    monkeypatch.setattr(j, "_connect", lambda: Connection(db))
    return j, db


def test_daylight_saving_opening_window_and_weekends():
    assert opening_window(NOW) == OPEN
    assert opening_window(OPEN) == OPEN
    assert opening_window(OPEN + timedelta(minutes=4, seconds=59)) == OPEN
    assert opening_window(OPEN + timedelta(minutes=5)) is None
    assert opening_window(OPEN - timedelta(seconds=1)) is None
    assert opening_window(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)) == (
        datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))
    assert opening_window(datetime(2026, 10, 11, 13, 30, tzinfo=timezone.utc)) is None
    with pytest.raises(TradeBlocked):
        opening_window(datetime(2026, 10, 12, 9, 31))


def test_opening_snapshot_written_once_with_switches_disabled(monkeypatch):
    j, db = journal(monkeypatch)
    broker = Broker()
    first = snapshot_one_opening(broker, j, now=NOW, after_read_now=NOW)
    assert first.state == "recorded"
    assert first.wrote_equity_snapshot is True
    assert db["checked_role"] == 2  # entry permission check + journal check
    assert len(db["snapshots"]) == 1
    assert db["snapshots"][0][0] == NOW
    assert db["snapshots"][0][1] == Decimal("500")
    assert db["snapshots"][0][2] == Decimal("500")
    second = snapshot_one_opening(broker, j, now=NOW, after_read_now=NOW)
    assert second.state == "already_recorded"
    assert len(db["snapshots"]) == 1
    assert broker.calls == ["clock", "account", "clock", "account"]
    assert any("pg_advisory_xact_lock" in sql for sql, _ in db["sql"])
    assert all("paper_control SET" not in sql for sql, _ in db["sql"])


def test_expired_github_cron_blocks_late_backfill(monkeypatch):
    j, db = journal(monkeypatch, db_now=OPEN + timedelta(minutes=5))
    with pytest.raises(TradeBlocked, match="window missed"):
        j.record_opening_equity(
            session_open_utc=OPEN, equity=500, cash=500)
    assert db["snapshots"] == []


@pytest.mark.parametrize("switches", [
    (True, False), (False, True), (False, False), (True, True),
])
def test_equity_baseline_is_independent_of_order_switches(
        monkeypatch, switches):
    # Synthetic controls ONLY. Production continues kill ON/submit OFF.
    j, db = journal(monkeypatch, switches=switches)
    assert j.record_opening_equity(
        session_open_utc=OPEN, equity=500, cash=500)
    assert len(db["snapshots"]) == 1
    assert db["switches"] == switches
    assert all("UPDATE public.paper_control" not in sql
               and "INSERT INTO public.paper_intents" not in sql
               for sql, _ in db["sql"])


@pytest.mark.parametrize("invalid", [
    (None, False), (True, None), ("false", True),
    (False, "true"), (1, 0), (False, 1),
])
def test_equity_writer_rejects_malformed_control_snapshot(
        monkeypatch, invalid):
    j, db = journal(monkeypatch, switches=invalid)
    with pytest.raises(TradeBlocked, match="Unverifiable PAPER control"):
        j.record_opening_equity(
            session_open_utc=OPEN, equity=500, cash=500)
    assert db["snapshots"] == []


def test_closed_market_cannot_create_snapshot(monkeypatch):
    j, db = journal(monkeypatch)
    result = snapshot_one_opening(
        Broker(open=False), j, now=NOW, after_read_now=NOW)
    assert result.state == "broker_market_not_open"
    assert db["snapshots"] == []


def test_github_delay_after_broker_reads_blocks(monkeypatch):
    j, db = journal(monkeypatch)
    with pytest.raises(TradeBlocked, match="expired"):
        snapshot_one_opening(
            Broker(), j, now=NOW, after_read_now=OPEN + timedelta(minutes=5))
    assert db["snapshots"] == []


def test_equity_on_weekend_skips_even_with_fake_open_clock(monkeypatch):
    j, db = journal(monkeypatch)
    broker = Broker(open=True)
    day = datetime(2026, 10, 11, 13, 31, tzinfo=timezone.utc)
    result = snapshot_one_opening(
        broker, j, now=day, after_read_now=day)
    assert result.state == "outside_opening_window"
    assert broker.calls == []
    assert db["sql"] == []


@pytest.mark.parametrize("equity,cash", [
    (0, 0), ("NaN", 500), (500, -1), (500, 600),
])
def test_invalid_opening_balances_refuse_database_write(monkeypatch, equity, cash):
    j, db = journal(monkeypatch)
    broker = Broker(equity=equity, cash=cash)
    with pytest.raises(TradeBlocked):
        snapshot_one_opening(broker, j, now=NOW, after_read_now=NOW)
    assert db["snapshots"] == []


def test_role_failure_aborts_before_alpaca_calls(monkeypatch):
    j, db = journal(monkeypatch)
    broker = Broker()
    def fail():
        raise TradeBlocked("Executor role permission failure")
    monkeypatch.setattr(j, "assert_executor_permissions", fail)
    with pytest.raises(TradeBlocked):
        snapshot_one_opening(broker, j, now=NOW, after_read_now=NOW)
    assert broker.calls == []
    assert db["snapshots"] == []
