"""Unit-only read-only supervisor: never opens a real broker/DB session."""
import copy
from pathlib import Path

import pytest

from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked
from bot.paper_supervisor_loop import (
    one_supervisor_tick, run_supervisor_loop, valid_observation,
)


WORKER = "paper-worker-2026-test001"


def safe_report():
    return {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "broker_paper_url": PAPER_ENDPOINT,
        "can_submit_orders": False,
        "broker_write_operations": 0,
        "reconciliation": {
            "consistent": True, "blockers": [],
            "uncovered_symbols": [],
        },
        "journal_kill_switch_active": True,
        "journal_submissions_enabled": False,
        "code_submissions_enabled": False,
    }


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
