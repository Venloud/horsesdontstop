"""Offline tests for opt-in broker-flat/closed Neon idle. No real calls."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.paper_neon_idle import (
    broker_confirms_closed_flat, verified_flat_journal_baseline,
)
from bot.monitor_policy import assess_read_only_report
from bot.paper_safety import PAPER_ENDPOINT
from bot.paper_supervisor_loop import run_supervisor_loop


def clean_report():
    report = {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "broker_paper_url": PAPER_ENDPOINT,
        "can_submit_orders": False,
        "broker_write_operations": 0,
        "journal_kill_switch_active": True,
        "journal_submissions_enabled": False,
        "code_submissions_enabled": False,
        "safety_blockers": [
            "kill_switch_active", "paper_order_submission_disabled",
            "external_cash_flows_unverified", "equity_history_unverified",
            "nyse_calendar_unverified",
            "preflight_has_no_broker_write_capability",
        ],
        "reconciliation": {
            "consistent": True, "blockers": [], "uncovered_symbols": [],
            "open_broker_positions": 0, "open_broker_orders": 0,
            "unresolved_journal_intents": 0,
        },
    }
    report["monitor_incident"] = assess_read_only_report(report).as_dict()
    return report

class FakePaperBroker:
    def __init__(self, *, opened=False, positions=(), orders=(), error=None):
        self.opened = opened
        self.positions = positions
        self.orders = orders
        self.error = error
        self.calls = []

    def get_clock(self):
        self.calls.append("get_clock")
        if self.error == "clock":
            raise ConnectionError("SECRET_ALPACA_CREDENTIAL")
        return SimpleNamespace(is_open=self.opened)

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        if self.error == "positions":
            raise ConnectionError("SECRET_ALPACA_CREDENTIAL")
        return self.positions

    def get_orders(self, *, filter):
        self.calls.append("get_orders")
        if self.error == "orders":
            raise ConnectionError("SECRET_ALPACA_CREDENTIAL")
        assert filter.limit == 500
        assert filter.nested is True
        assert filter.status.value == "open"
        return self.orders

    def submit_order(self, **_):
        raise AssertionError("NO broker order writes allowed")

    def cancel_order_by_id(self, **_):
        raise AssertionError("NO broker order cancellation allowed")


class FakeHeartbeat:
    def __init__(self, failures=()):
        self.pulses = []
        self.failures = set(failures)

    def pulse(self, *, worker_id, sequence, poll_succeeded):
        self.pulses.append((sequence, poll_succeeded))
        return bool(poll_succeeded and sequence not in self.failures)


def test_closed_flat_probe_checks_actual_broker_status_each_time():
    broker = FakePaperBroker(opened=False, positions=[], orders=[])
    assert broker_confirms_closed_flat(broker) is True
    assert broker.calls == ["get_clock", "get_all_positions", "get_orders"]
    assert broker_confirms_closed_flat(broker) is True
    assert broker.calls == [
        "get_clock", "get_all_positions", "get_orders",
        "get_clock", "get_all_positions", "get_orders",
    ]


@pytest.mark.parametrize("change", [
    {"opened": True}, {"opened": None}, {"opened": "false"},
    {"positions": [object()]}, {"orders": [object()]},
    {"positions": None}, {"orders": None},
    {"positions": {"unknown": "shape"}},
    {"error": "clock"}, {"error": "positions"}, {"error": "orders"},
])
def test_uncertain_broker_never_allows_neon_idle(change):
    broker = FakePaperBroker(opened=False, positions=[], orders=[], **{
        k: v for k, v in change.items() if k not in
        ("opened", "positions", "orders")})
    for name in ("opened", "positions", "orders"):
        if name in change:
            setattr(broker, name, change[name])
    assert broker_confirms_closed_flat(broker) is False


@pytest.mark.parametrize("field,value", [
    ("journal_kill_switch_active", False),
    ("journal_submissions_enabled", True),
    ("code_submissions_enabled", True),
    ("can_submit_orders", True),
])
def test_only_fully_disabled_and_reconciled_flat_journal_baseline(field, value):
    report = clean_report()
    assert verified_flat_journal_baseline(report)
    report[field] = value
    assert not verified_flat_journal_baseline(report)


@pytest.mark.parametrize("field,value", [
    ("open_broker_positions", 1),
    ("open_broker_orders", 1),
    ("unresolved_journal_intents", 1),
    ("open_broker_positions", False),
    ("open_broker_orders", None),
    ("unresolved_journal_intents", "0"),
])
def test_nonflat_or_malformed_journal_cannot_enable_neon_idle(field,value):
    report = clean_report()
    report["reconciliation"][field] = value
    assert not verified_flat_journal_baseline(report)


def test_process_must_first_observe_full_healthy_neon_poll():
    broker = FakePaperBroker(opened=False, positions=[], orders=[])
    writer = FakeHeartbeat()
    full_calls = []
    def full():
        full_calls.append(True)
        return clean_report()
    result = run_supervisor_loop(
        fetch_read_only_report=full,
        heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=lambda: broker_confirms_closed_flat(broker),
        max_ticks=4, sleeper=lambda _: None)
    # Tick 1 always consults Neon. Subsequent idle ticks use ONLY broker GETs.
    assert result.ticks == 4
    assert result.healthy_polls == 1
    assert result.neon_idle_ticks == 3
    assert result.blocked_polls == 0
    assert len(full_calls) == 1
    assert [p[0] for p in writer.pulses] == [1]
    assert broker.calls == [
        "get_clock", "get_all_positions", "get_orders",
        "get_clock", "get_all_positions", "get_orders",
        "get_clock", "get_all_positions", "get_orders",
    ]
    assert result.broker_order_submissions == 0


def test_open_market_forces_neon_check_and_cannot_be_reported_idle():
    broker = FakePaperBroker(opened=True, positions=[], orders=[])
    writer = FakeHeartbeat()
    polls = []
    result = run_supervisor_loop(
        fetch_read_only_report=lambda: (polls.append(1), clean_report())[1],
        heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=lambda: broker_confirms_closed_flat(broker),
        max_ticks=3, sleeper=lambda _: None)
    assert len(polls) == 3
    assert len(writer.pulses) == 3
    assert result.neon_idle_ticks == 0
    assert result.healthy_polls == 3


def test_failed_heartbeat_does_not_establish_idle_baseline():
    writer = FakeHeartbeat(failures={1})
    probes = []
    def probe():
        probes.append(True)
        return True
    reports = []
    report = run_supervisor_loop(
        fetch_read_only_report=lambda: (reports.append(True), clean_report())[1],
        heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=probe, max_ticks=3,
        sleeper=lambda _: None)
    assert len(reports) == 2
    assert len(probes) == 1
    assert report.blocked_polls == 1
    assert report.healthy_polls == 1
    assert report.neon_idle_ticks == 1


def test_unverified_full_poll_clears_cached_baseline():
    writer = FakeHeartbeat()
    reports = [clean_report(), clean_report(), clean_report()]
    reports[1]["reconciliation"]["unresolved_journal_intents"] = 1
    state = {"probe": 0, "full": 0}

    def probe():
        state["probe"] += 1
        return False if state["probe"] == 1 else True

    def full():
        idx = state["full"]
        state["full"] += 1
        return reports[idx]
    result = run_supervisor_loop(
        fetch_read_only_report=full, heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=probe, max_ticks=3,
        sleeper=lambda _: None)
    assert state == {"probe": 1, "full": 3}
    assert result.neon_idle_ticks == 0
    assert result.blocked_polls == 1
    assert result.healthy_polls == 2


def test_probe_crash_falls_back_to_neon_without_leaking_secret(capsys):
    calls = []
    def broken_probe():
        raise RuntimeError("SECRET_POSTGRES_URL_IN_EXCEPTION")
    r = run_supervisor_loop(
        fetch_read_only_report=lambda: (calls.append(1), clean_report())[1],
        heartbeat_writer=FakeHeartbeat(), external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=broken_probe,
        max_ticks=2, sleeper=lambda _: None)
    assert len(calls) == 2
    assert r.neon_idle_ticks == 0
    assert "SECRET_POSTGRES_URL_IN_EXCEPTION" not in str(capsys.readouterr())


def test_idle_logs_do_not_fake_healthy_heartbeats(capsys):
    writer = FakeHeartbeat()
    result = run_supervisor_loop(
        fetch_read_only_report=clean_report, heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=lambda: True,
        max_ticks=2, sleeper=lambda _: None)
    log = capsys.readouterr().out
    assert result.neon_idle_ticks == 1
    assert result.healthy_polls == 1
    assert "NEON IDLE, NO heartbeat, NO trading permission" in log
    assert len(writer.pulses) == 1


def test_default_path_unmodified_with_no_idle_probe():
    writer = FakeHeartbeat()
    result = run_supervisor_loop(
        fetch_read_only_report=clean_report, heartbeat_writer=writer,
        max_ticks=3, sleeper=lambda _: None)
    assert result.ticks == 3
    assert result.healthy_polls == 3
    assert result.neon_idle_ticks == 0
    assert len(writer.pulses) == 3


def test_replayed_idle_report_cannot_unlock_neon_skip():
    from datetime import timedelta
    report = clean_report()
    report["observed_at_utc"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    writer = FakeHeartbeat()
    called = []
    result = run_supervisor_loop(
        fetch_read_only_report=lambda: report,
        heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=lambda: called.append(True) or True,
        max_ticks=3, sleeper=lambda _: None)
    assert called == []
    assert result.blocked_polls == 3
    assert result.neon_idle_ticks == 0
    assert all(ok is False for _, ok in writer.pulses)


def test_forged_idle_incident_fingerprint_never_skips_neon():
    report = clean_report()
    report["monitor_incident"]["fingerprint"] = "0" * 32
    writer = FakeHeartbeat()
    result = run_supervisor_loop(
        fetch_read_only_report=lambda: report,
        heartbeat_writer=writer,
        external_liveness_sink=lambda **kw: True,
        closed_flat_broker_probe=lambda: True,
        max_ticks=3, sleeper=lambda _: None)
    assert result.healthy_polls == 0
    assert result.blocked_polls == 3
    assert result.neon_idle_ticks == 0


def test_closed_flat_idle_requires_external_signal_before_any_observation():
    from bot.paper_safety import TradeBlocked
    writer = FakeHeartbeat()
    calls = []
    with pytest.raises(TradeBlocked, match="external liveness"):
        run_supervisor_loop(
            fetch_read_only_report=lambda: calls.append(True),
            heartbeat_writer=writer, closed_flat_broker_probe=lambda: True,
            max_ticks=1, sleeper=lambda _: None)
    assert writer.pulses == []
    assert calls == []


def test_external_liveness_failure_invalidates_idle_baseline(capsys):
    writer = FakeHeartbeat()
    signals = []
    reports = []
    def broken_signal(**details):
        signals.append(details["status"])
        raise RuntimeError("SECRET_SIGNAL_TOKEN")
    result = run_supervisor_loop(
        fetch_read_only_report=lambda: (reports.append(1), clean_report())[1],
        heartbeat_writer=writer,
        closed_flat_broker_probe=lambda: True,
        external_liveness_sink=broken_signal, max_ticks=3,
        sleeper=lambda _: None)
    assert result.external_liveness_failures == 3
    assert result.neon_idle_ticks == 0
    assert len(reports) == 3
    assert signals == ["read_only_poll_ok", "read_only_poll_ok", "read_only_poll_ok"]
    assert "SECRET_SIGNAL_TOKEN" not in str(capsys.readouterr())


def test_successful_external_idle_signals_are_not_trading_heartbeat():
    writer = FakeHeartbeat()
    received = []
    result = run_supervisor_loop(
        fetch_read_only_report=clean_report,
        heartbeat_writer=writer, closed_flat_broker_probe=lambda: True,
        external_liveness_sink=lambda **details: received.append(
            details["status"]) or True,
        max_ticks=3, sleeper=lambda _: None)
    assert result.neon_idle_ticks == 2
    assert result.external_liveness_failures == 0
    assert result.healthy_polls == 1
    assert received == [
        "read_only_poll_ok", "closed_flat_idle", "closed_flat_idle"]
    assert len(writer.pulses) == 1
