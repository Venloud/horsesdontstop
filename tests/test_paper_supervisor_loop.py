"""Unit-only read-only supervisor: never opens a real broker/DB session."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.monitor_policy import assess_read_only_report
from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked
from bot.paper_supervisor_loop import (
    one_supervisor_tick, run_supervisor_loop, valid_observation,
)


WORKER = "paper-worker-2026-test001"


def safe_report():
    report = {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "broker_paper_url": PAPER_ENDPOINT,
        "can_submit_orders": False,
        "code_submissions_enabled": False,
        "broker_write_operations": 0,
        "journal_kill_switch_active": True,
        "journal_submissions_enabled": False,
        "safety_blockers": [
            "kill_switch_active", "paper_order_submission_disabled",
            "external_cash_flows_unverified", "equity_history_unverified",
            "nyse_calendar_unverified",
            "preflight_has_no_broker_write_capability",
        ],
        "reconciliation": {
            "consistent": True, "blockers": [],
            "open_broker_positions": 0, "open_broker_orders": 0,
            "unresolved_journal_intents": 0,
            "uncovered_symbols": [],
        },
    }
    # Synthetic classifier output from the exact same read-only
    # policy used by run_once; not an authenticated external report.
    report["monitor_incident"] = assess_read_only_report(report).as_dict()
    return report

class FakeWriter:
    def __init__(self):
        self.pulses = []
        self.return_first_unsafe = True

    def pulse(self, *, worker_id, sequence, poll_succeeded):
        self.pulses.append((worker_id, sequence, poll_succeeded))
        if self.return_first_unsafe and sequence == 1:
            return False
        return poll_succeeded


def test_valid_read_only_paper_observation_is_only_monitor_health():
    report = safe_report()
    assert valid_observation(report)
    assert report["journal_kill_switch_active"] is True
    assert report["journal_submissions_enabled"] is False


@pytest.mark.parametrize("changes", [
    {"mode": "LIVE_MONEY"},
    {"broker_paper_url": "https://api.alpaca.markets"},
    {"can_submit_orders": True},
    {"can_submit_orders": None},
    {"broker_write_operations": 1},
    {"reconciliation": None},
    {"reconciliation": {"consistent": False, "blockers": [],
                        "uncovered_symbols": []}},
    {"reconciliation": {"consistent": True, "blockers": ["orphan_stop"],
                        "uncovered_symbols": []}},
    {"reconciliation": {"consistent": True, "blockers": [],
                        "uncovered_symbols": ["FRC"]}},
])
def test_unsafe_lookalike_report_cannot_attest_healthy_broker_poll(changes):
    r = safe_report()
    r.update(changes)
    assert valid_observation(r) is False




@pytest.mark.parametrize("change", [
    lambda p: p.update(journal_kill_switch_active=False),
    lambda p: p.update(journal_submissions_enabled=True),
    lambda p: p.update(code_submissions_enabled=True),
    lambda p: p.update(broker_write_operations=False),
    lambda p: p.update(monitor_incident={"level": "idle", "code": "idle"}),
    lambda p: p["reconciliation"].update(open_broker_positions=1),
    lambda p: p["reconciliation"].update(open_broker_orders=1),
    lambda p: p["reconciliation"].update(unresolved_journal_intents=1),
    lambda p: p["safety_blockers"].append("unrecognized_monitor_failure"),
])
def test_heartbeat_refuses_reconciled_looking_but_unsafe_report(change):
    p = safe_report()
    change(p)
    assert valid_observation(p) is False


def test_heartbeat_refuses_replayed_future_or_missing_timestamp():
    p = safe_report()
    observed = datetime.fromisoformat(p["observed_at_utc"])
    assert valid_observation(p, now=observed + timedelta(seconds=120))
    assert valid_observation(p, now=observed + timedelta(seconds=121)) is False
    assert valid_observation(p, now=observed - timedelta(seconds=1)) is False
    p.pop("observed_at_utc")
    assert valid_observation(p) is False


def test_contradictory_idle_incident_fails_unhealthy_worker_pulse():
    p = safe_report()
    p["journal_submissions_enabled"] = True
    writer = FakeWriter()
    assert not one_supervisor_tick(
        fetch_read_only_report=lambda: p, heartbeat_writer=writer,
        worker_id=WORKER, sequence=2)
    assert writer.pulses == [(WORKER, 2, False)]

def test_broker_exception_only_sends_unhealthy_heartbeat():
    w = FakeWriter()

    def fails():
        raise RuntimeError("SECRET_ALPACA_KEY_IN_ERROR")
    assert not one_supervisor_tick(
        fetch_read_only_report=fails, heartbeat_writer=w,
        worker_id=WORKER, sequence=1)
    assert w.pulses == [(WORKER, 1, False)]


def test_missing_or_malformed_report_never_claims_healthy_worker():
    for result in (None, {}, [], "okay"):
        w = FakeWriter()
        assert not one_supervisor_tick(
            fetch_read_only_report=lambda: result,
            heartbeat_writer=w, worker_id=WORKER, sequence=1)
        assert w.pulses == [(WORKER, 1, False)]


def test_heartbeat_writer_outage_returns_false_without_order():
    class BrokenWriter:
        def pulse(self, **_args):
            raise ConnectionError("postgresql://SECRET_PASSWORD")
    assert not one_supervisor_tick(
        fetch_read_only_report=safe_report, heartbeat_writer=BrokenWriter(),
        worker_id=WORKER, sequence=1)


def test_bounded_worker_loop_does_not_send_any_broker_orders(capsys):
    w = FakeWriter()
    sleeps = []
    result = run_supervisor_loop(
        fetch_read_only_report=safe_report, heartbeat_writer=w,
        worker_id=WORKER, max_ticks=3, interval_seconds=30,
        sleeper=sleeps.append)
    assert result.ticks == 3
    assert result.healthy_polls == 2
    assert result.blocked_polls == 1
    assert result.broker_order_submissions == 0
    assert w.pulses == [
        (WORKER, 1, True), (WORKER, 2, True), (WORKER, 3, True)
    ]
    assert sleeps == [30, 30]
    captured = capsys.readouterr()
    assert "ZERO broker orders" in captured.out
    assert "NO trading permission" in captured.err


@pytest.mark.parametrize("seconds,ticks", [
    (29, 2), (91, 2), (30, -1), (30, 501),
    (30.0, 2), (True, 2), (30, True),
])
def test_invalid_poll_cadence_and_bounds_fail_before_any_io(seconds, ticks):
    w = FakeWriter()
    with pytest.raises(TradeBlocked):
        run_supervisor_loop(
            fetch_read_only_report=lambda: (_ for _ in ()).throw(
                AssertionError("should not poll")),
            heartbeat_writer=w, interval_seconds=seconds,
            max_ticks=ticks, sleeper=lambda _seconds: None)
    assert w.pulses == []


def test_a_new_worker_instance_is_generated_for_each_process_start():
    seen = []

    class CollectWriter:
        def pulse(self, **kwargs):
            seen.append(kwargs["worker_id"])
            return False

    for _ in range(2):
        run_supervisor_loop(
            fetch_read_only_report=safe_report,
            heartbeat_writer=CollectWriter(),
            max_ticks=1, sleeper=lambda _seconds: None)
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(x.startswith("paper-") for x in seen)


def test_read_only_worker_imports_no_order_submission_client():
    import bot.paper_supervisor_loop as module
    source = Path(module.__file__).read_text()
    assert "TradingClient(" not in source
    assert ".submit_order(" not in source
    assert ".cancel_order(" not in source
    assert ".close_all_positions(" not in source
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = True" not in source


def test_broker_failure_pages_and_keeps_order_release_disabled():
    messages = []
    writer = FakeWriter()
    def broken():
        raise RuntimeError("postgresql://VERY_SECRET")
    result = run_supervisor_loop(
        fetch_read_only_report=broken, heartbeat_writer=writer,
        worker_id=WORKER, interval_seconds=30, max_ticks=2,
        sleeper=lambda _s: None,
        critical_alert_sink=lambda code: messages.append(code) or True)
    assert result.blocked_polls == 2
    assert result.broker_order_submissions == 0
    assert result.alert_delivery_failures == 0
    assert messages == ["supervisor_poll_or_lease_failed"] * 2
    assert all(pulse[2] is False for pulse in writer.pulses)


def test_external_deadman_failure_triggers_critical_alert():
    observed = []
    writer = FakeWriter()
    result = run_supervisor_loop(
        fetch_read_only_report=safe_report, heartbeat_writer=writer,
        worker_id=WORKER, max_ticks=2, sleeper=lambda _s: None,
        external_liveness_sink=lambda **_details: False,
        critical_alert_sink=lambda code: observed.append(code) or True)
    assert result.external_liveness_failures == 2
    assert observed == ["supervisor_poll_or_lease_failed",
                        "external_liveness_unverified",
                        "external_liveness_unverified"]
    assert result.broker_order_submissions == 0


def test_alert_exception_is_redacted_and_cannot_claim_success(capsys):
    def leaking(_code):
        raise RuntimeError("https://ntfy.sh/SUPER_SECRET_TOPIC")
    result = run_supervisor_loop(
        fetch_read_only_report=lambda: None,
        heartbeat_writer=FakeWriter(), worker_id=WORKER,
        interval_seconds=30, max_ticks=1,
        critical_alert_sink=leaking)
    assert result.blocked_polls == 1
    assert result.alert_delivery_failures == 1
    assert "SUPER_SECRET_TOPIC" not in capsys.readouterr().err


def test_healthy_second_lease_pulse_never_pages():
    notices = []
    result = run_supervisor_loop(
        fetch_read_only_report=safe_report,
        heartbeat_writer=FakeWriter(), worker_id=WORKER,
        max_ticks=3, sleeper=lambda _s: None,
        critical_alert_sink=lambda code: notices.append(code) or True)
    assert result.healthy_polls == 2
    assert notices == ["supervisor_poll_or_lease_failed"]


def test_failed_external_delivery_receipt_cannot_claim_acknowledgement():
    events=[]
    def delivery(**kw):raise RuntimeError("secret transport detail")
    report=run_supervisor_loop(fetch_read_only_report=safe_report,heartbeat_writer=FakeWriter(),
        max_ticks=1,external_liveness_sink=delivery,tick_observer=events.append)
    assert report.external_liveness_failures==1
    assert events[0]['external_ack'] is False and events[0]['status']=='poll_failed'


def test_runtime_receipt_failure_alerts_and_stops_before_next_tick():
    alarms=[];writer=FakeWriter()
    def failure(event):raise OSError('sensitive path')
    with pytest.raises(TradeBlocked,match='receipt persistence failed'):
        run_supervisor_loop(fetch_read_only_report=safe_report,heartbeat_writer=writer,
            max_ticks=2,tick_observer=failure,critical_alert_sink=lambda code:alarms.append(code) or True)
    assert 'runtime_receipt_unavailable' in alarms and len(writer.pulses)==1
