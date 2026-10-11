"""Offline tests: worker lease cannot release PAPER order submissions."""
from datetime import datetime, timedelta, timezone

import pytest

from bot.paper_safety import TradeBlocked
from bot.paper_worker_lease import (
    HEARTBEAT_ROLE, PaperWorkerHeartbeat,
)

NOW = datetime(2026, 10, 12, 14, 40, tzinfo=timezone.utc)
W1 = "paper-worker-instance-A"
W2 = "paper-worker-instance-B"


class Cursor:
    def __init__(self, data):
        self.data = data
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.data["queries"].append((sql, params))
        if sql.startswith("SELECT current_user"):
            self.last = self.data["permissions"]
        elif sql.startswith("SET LOCAL statement_timeout"):
            self.last = None
        elif sql.startswith("SELECT clock_timestamp()"):
            prev = self.data["lease"]
            self.last = ((self.data["now"], *prev) if prev else None)
        elif sql.startswith("INSERT INTO public.paper_worker_heartbeat"):
            worker, seq, success = params
            now = self.data["now"]
            prev = self.data["lease"]
            if prev is None:
                self.data["lease"] = (worker, seq, now, False)
                self.last = (False,)
            elif prev[0] == worker and seq > prev[1]:
                self.data["lease"] = (worker, seq, now, success)
                self.last = (success,)
            elif (prev[0] != worker
                  and (now - prev[2]).total_seconds() > 120):
                self.data["lease"] = (worker, seq, now, False)
                self.last = (False,)
            else:
                self.last = None
        else:
            raise AssertionError("Unexpected query in restricted lease: " + sql)

    def fetchone(self):
        return self.last


class Conn:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return Cursor(self.data)


def lease(monkeypatch, permissions=None):
    data = {
        "queries": [], "now": NOW, "lease": None,
        "permissions": permissions or
        (HEARTBEAT_ROLE, False, False, False, False, True, True, False),
    }
    h = PaperWorkerHeartbeat("postgresql://synthetic.invalid/neondb")
    monkeypatch.setattr(h, "_connect", lambda: Conn(data))
    return h, data


def test_absent_heartbeat_does_not_claim_healthy_worker(monkeypatch):
    h, d = lease(monkeypatch)
    result = h.inspect(expected_worker_id=W1)
    assert result.healthy is False
    assert result.watchdog_heartbeat_seconds is None
    assert "persistent_supervisor_heartbeat_missing" in result.blockers


def test_two_successful_pulses_can_prove_recently_polled_worker(monkeypatch):
    h, d = lease(monkeypatch)
    assert h.pulse(worker_id=W1, sequence=1, poll_succeeded=True) is False
    first = h.inspect(expected_worker_id=W1)
    assert first.healthy is False
    assert "persistent_supervisor_broker_poll_not_proven" in first.blockers
    d["now"] += timedelta(seconds=30)
    assert h.pulse(worker_id=W1, sequence=2, poll_succeeded=True) is True
    result = h.inspect(expected_worker_id=W1)
    assert result.healthy is True
    assert result.sequence == 2
    assert result.watchdog_heartbeat_seconds == 0
    assert result.blockers == ()
    assert all("paper_worker_heartbeat" in sql
               or sql.startswith("SET LOCAL")
               or sql.startswith("SELECT current_user")
               for sql, _ in d["queries"])


def test_failed_broker_poll_makes_new_lease_unhealthy(monkeypatch):
    h, d = lease(monkeypatch)
    h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    h.pulse(worker_id=W1, sequence=2, poll_succeeded=False)
    result = h.inspect(expected_worker_id=W1)
    assert result.watchdog_heartbeat_seconds is None
    assert "persistent_supervisor_broker_poll_not_proven" in result.blockers


def test_duplicate_sequence_never_refreshes_lease(monkeypatch):
    h, d = lease(monkeypatch)
    h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    d["now"] += timedelta(seconds=20)
    with pytest.raises(TradeBlocked, match="Duplicate or competing"):
        h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    assert d["lease"][2] == NOW


def test_two_workers_cannot_both_hold_fresh_lease(monkeypatch):
    h, d = lease(monkeypatch)
    h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    with pytest.raises(TradeBlocked, match="competing"):
        h.pulse(worker_id=W2, sequence=1, poll_succeeded=True)
    assert h.inspect(expected_worker_id=W2).healthy is False
    assert "persistent_supervisor_identity_mismatch" in (
        h.inspect(expected_worker_id=W2).blockers)


def test_stale_other_worker_takeover_has_mandatory_unhealthy_first_pulse(monkeypatch):
    h, d = lease(monkeypatch)
    h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    h.pulse(worker_id=W1, sequence=2, poll_succeeded=True)
    d["now"] += timedelta(seconds=121)
    assert h.inspect(expected_worker_id=W1).watchdog_heartbeat_seconds is None
    assert h.pulse(worker_id=W2, sequence=1, poll_succeeded=True) is False
    assert h.inspect(expected_worker_id=W2).healthy is False
    d["now"] += timedelta(seconds=20)
    assert h.pulse(worker_id=W2, sequence=2, poll_succeeded=True) is True
    assert h.inspect(expected_worker_id=W2).healthy is True


@pytest.mark.parametrize("worker,sequence,success", [
    ("short", 1, True), ("x" * 65, 1, True),
    ("contains space", 1, True), (W1, 0, True),
    (W1, -1, True), (W1, 1.0, True),
    (W1, True, True), (W1, 1, "true"),
])
def test_invalid_pulse_arguments_never_touch_database(
        monkeypatch, worker, sequence, success):
    h, d = lease(monkeypatch)
    with pytest.raises(TradeBlocked):
        h.pulse(worker_id=worker, sequence=sequence, poll_succeeded=success)
    assert d["queries"] == []


@pytest.mark.parametrize("permissions", [
    ("neondb_owner", True, True, True, True, True, True, True),
    (HEARTBEAT_ROLE, False, False, True, False, True, True, False),
    (HEARTBEAT_ROLE, False, False, False, True, True, True, False),
    (HEARTBEAT_ROLE, False, False, False, False, True, True, True),
    (HEARTBEAT_ROLE, False, False, False, False, False, True, False),
])
def test_writer_needs_exact_narrow_role(monkeypatch, permissions):
    h, d = lease(monkeypatch, permissions=permissions)
    with pytest.raises(TradeBlocked, match="permissions"):
        h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    assert d["lease"] is None


def test_stale_missing_and_future_db_time_are_blockers(monkeypatch):
    h, d = lease(monkeypatch)
    h.pulse(worker_id=W1, sequence=1, poll_succeeded=True)
    h.pulse(worker_id=W1, sequence=2, poll_succeeded=True)
    d["now"] += timedelta(seconds=121)
    result = h.inspect(expected_worker_id=W1)
    assert result.watchdog_heartbeat_seconds is None
    assert "persistent_supervisor_heartbeat_stale_or_future" in result.blockers
    d["now"] = NOW - timedelta(seconds=1)
    result = h.inspect(expected_worker_id=W1)
    assert result.watchdog_heartbeat_seconds is None
    assert "persistent_supervisor_heartbeat_stale_or_future" in result.blockers


def test_worker_lease_code_is_not_alpaca_broker_order_gateway():
    from pathlib import Path
    import bot.paper_worker_lease as module
    source = Path(module.__file__).read_text()
    assert "import alpaca" not in source
    assert "TradingClient(" not in source
    assert ".submit_order(" not in source
    assert "paper_control SET" not in source
    assert "UPDATE public.paper_intents" not in source
