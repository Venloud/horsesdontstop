"""Synthetic persistent equity collector: no real Alpaca or Neon calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.paper_opening_equity_daemon import inspect_equity_window, run_equity_daemon
from bot.paper_safety import TradeBlocked

OPEN = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)


class Broker:
    def __init__(self, opened=True):
        self.opened = opened
        self.calls = []

    def get_clock(self):
        self.calls.append("clock")
        return SimpleNamespace(is_open=self.opened)

    def get_account(self):
        self.calls.append("account")
        return SimpleNamespace(equity="500", cash="500")

    def submit_order(self, **_kwargs):
        raise AssertionError("PAPER equity collection must never order")


class Journal:
    def __init__(self, exists=False):
        self.exists = exists
        self.calls = 0

    def assert_executor_permissions(self):
        self.calls += 1

    def read_equity_references(self, start, now):
        return (500 if self.exists else None), 500

    def record_opening_equity(self, *, session_open_utc, equity, cash):
        self.exists = True
        return True


def test_live_window_captures_only_once():
    j = Journal()
    b = Broker()
    now = OPEN + timedelta(seconds=3)
    first = inspect_equity_window(trading=b, journal=j, now=now,
                                  clock=lambda: now)
    assert first.state == "recorded"
    assert j.exists is True
    second = inspect_equity_window(
        trading=b, journal=j, now=OPEN + timedelta(minutes=5),
        clock=lambda: OPEN + timedelta(minutes=5))
    assert second == "confirmed"


def test_missed_window_is_critical_and_never_backfilled():
    j = Journal()
    b = Broker()
    late = OPEN + timedelta(minutes=5)
    assert inspect_equity_window(trading=b, journal=j, now=late,
                                 clock=lambda: late) == "missing_opening_equity"
    assert j.exists is False
    assert b.calls == ["clock"]


def test_weekend_and_closed_market_are_not_assumed_open():
    weekend = datetime(2026, 10, 18, 13, 35, tzinfo=timezone.utc)
    assert inspect_equity_window(trading=Broker(), journal=Journal(),
                                 now=weekend, clock=lambda: weekend) == "outside_opening_window"
    closed = OPEN + timedelta(minutes=5)
    assert inspect_equity_window(trading=Broker(opened=False), journal=Journal(),
                                 now=closed, clock=lambda: closed) == "market_closed"


def test_unknown_broker_market_fails_closed():
    current = OPEN + timedelta(minutes=5)
    assert inspect_equity_window(trading=Broker(opened=None), journal=Journal(),
                                 now=current, clock=lambda: current) == "unverifiable_opening_equity"


def test_repeating_worker_alerts_on_missing_opening_without_broker_writes():
    dates = iter([OPEN + timedelta(minutes=5), OPEN + timedelta(minutes=5, seconds=15)])
    notices = []
    b, j = Broker(), Journal()
    result = run_equity_daemon(
        trading=b, journal=j, clock=lambda: next(dates),
        sleeper=lambda _seconds: None, max_ticks=2,
        critical_alert_sink=lambda code: notices.append(code) or True)
    assert result.ticks == 2
    assert result.missed_sessions == 1
    assert notices == ["missing_opening_equity"]
    assert b.calls == ["clock", "clock"]


@pytest.mark.parametrize("interval,ticks", [(9,1),(61,1),(15,-1),(15,501),
                                            (15.0,1),(True,1),(15,True)])
def test_bad_configuration_never_polls(interval,ticks):
    with pytest.raises(TradeBlocked):
        run_equity_daemon(trading=Broker(), journal=Journal(),
                          interval_seconds=interval, max_ticks=ticks,
                          clock=lambda: (_ for _ in ()).throw(AssertionError("IO")))


def test_no_alert_transport_is_not_silent_success():
    result = run_equity_daemon(
        trading=Broker(), journal=Journal(),
        clock=lambda: OPEN + timedelta(minutes=5),
        sleeper=lambda _s: None, max_ticks=1)
    assert result.alert_delivery_failures == 1


def test_missing_baseline_alert_survives_noon_restarts():
    noon = OPEN + timedelta(hours=2, minutes=35)
    b, j = Broker(), Journal()
    assert inspect_equity_window(trading=b, journal=j, now=noon,
                                 clock=lambda: noon) == "missing_opening_equity"
    notified = []
    result = run_equity_daemon(
        trading=b, journal=j, clock=lambda: noon, max_ticks=1,
        critical_alert_sink=lambda code: notified.append(code) or True)
    assert result.missed_sessions == 1
    assert notified == ["missing_opening_equity"]


def test_verified_baseline_is_cached_after_read_only_proof():
    at = OPEN + timedelta(minutes=5)
    later = OPEN + timedelta(minutes=10)
    b, j = Broker(), Journal(exists=True)
    dates = iter([at, later])
    result = run_equity_daemon(
        trading=b, journal=j, clock=lambda: next(dates),
        sleeper=lambda _s: None, max_ticks=2,
        critical_alert_sink=lambda _: True)
    assert result.confirmed_sessions == 1
    assert b.calls == ["clock"]


def test_naive_sampler_clock_rejected_before_broker_or_db_calls():
    with pytest.raises(TradeBlocked, match="clock unavailable or naive"):
        run_equity_daemon(trading=Broker(), journal=Journal(),
                          max_ticks=1, clock=lambda: datetime(2026,10,20,13,30))
