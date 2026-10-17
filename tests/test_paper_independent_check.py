"""No-network independent supervisor-loss detection and critical alert tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.paper_independent_check as mod
from bot.monitor_policy import assess_read_only_report


def flat_readonly_report(*, now=None):
    now = now or datetime.now(timezone.utc)
    report = {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": now.isoformat(),
        "broker_paper_url": "https://paper-api.alpaca.markets",
        "can_submit_orders": False,
        "code_submissions_enabled": False,
        "broker_write_operations": 0,
        "journal_kill_switch_active": True,
        "journal_submissions_enabled": False,
        "safety_blockers": [
            "kill_switch_active", "paper_order_submission_disabled",
            "market_closed", "no_healthy_persistent_watchdog",
            "preflight_has_no_broker_write_capability",
        ],
        "reconciliation": {
            "consistent": True, "blockers": [],
            "open_broker_positions": 0, "open_broker_orders": 0,
            "unresolved_journal_intents": 0, "uncovered_symbols": [],
        },
    }
    report["monitor_incident"] = assess_read_only_report(report).as_dict()
    return report


def lease(*, healthy=True, succeeded=True, age=0., sequence=2, blockers=()):
    return SimpleNamespace(
        healthy=healthy,
        poll_succeeded=succeeded,
        age_seconds=age,
        watchdog_heartbeat_seconds=int(age) if healthy and type(age) in (int, float) else None,
        blockers=blockers, sequence=sequence)


def test_flat_account_and_healthy_independent_lease_are_observer_only():
    incident = mod.evaluate_independent_supervisor(
        flat_readonly_report(), lease())
    assert incident.level == "idle"
    assert incident.code == "idle"


@pytest.mark.parametrize("bad", [
    None, lease(healthy=False), lease(succeeded=False),
    lease(age=121), lease(age=-1), lease(age=None),
    lease(age="0"), lease(sequence=1), lease(sequence=True),
    lease(blockers=("stale",)),
    SimpleNamespace(healthy=True),
])
def test_missing_stale_or_unknown_lease_is_critical_even_when_closed_flat(bad):
    incident = mod.evaluate_independent_supervisor(
        flat_readonly_report(), bad)
    assert incident.level == "critical"
    assert incident.code in ("supervisor_heartbeat_missing", "unverifiable")


@pytest.mark.parametrize("change", [
    lambda r: r.update(journal_kill_switch_active=False),
    lambda r: r.update(journal_submissions_enabled=True),
    lambda r: r.update(can_submit_orders=True),
    lambda r: r.update(observed_at_utc=(
        datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()),
    lambda r: r["reconciliation"].update(open_broker_positions=1),
    lambda r: r["reconciliation"].update(open_broker_orders=1),
    lambda r: r["reconciliation"].update(unresolved_journal_intents=1),
    lambda r: r["safety_blockers"].append("not_reconciled"),
])
def test_unverified_broker_journal_wins_over_nominally_healthy_lease(change):
    report = flat_readonly_report()
    change(report)
    incident = mod.evaluate_independent_supervisor(report, lease())
    assert incident.level == "critical"


def test_independent_checker_rejects_dynamic_or_invalid_expected_identity():
    checks = []
    incident = mod.independent_check(
        fetch_broker_report=lambda: checks.append("broker"),
        inspect_lease=lambda **kw: checks.append("lease"),
        expected_worker_id="unknown")
    assert incident.level == "critical"
    assert checks == []


def test_broker_exception_and_monitor_exception_are_critical_and_sanitized():
    def fails(**kwargs):
        raise RuntimeError("postgres://PRIVATE_NEON_SECRET")
    incident = mod.independent_check(
        fetch_broker_report=flat_readonly_report,
        inspect_lease=fails,
        expected_worker_id="paper-independent-verified-0001")
    assert incident.level == "critical"
    assert "PRIVATE_NEON_SECRET" not in str(incident)


def test_independent_checker_requires_a_matching_observed_lease():
    calls = []
    incident = mod.independent_check(
        fetch_broker_report=flat_readonly_report,
        inspect_lease=lambda *,expected_worker_id: (
            calls.append(expected_worker_id), lease())[1],
        expected_worker_id="paper-worker-test-2026")
    assert incident.level == "idle"
    assert calls == ["paper-worker-test-2026"]


def test_entrypoint_never_accepts_missing_readonly_credentials(monkeypatch,capsys):
    monkeypatch.delenv("PAPER_JOURNAL_DSN",raising=False)
    monkeypatch.setenv("PAPER_EXPECTED_WORKER_ID","paper-worker-2026-a")
    assert mod.main([]) == 2
    assert "BLOCKED" in capsys.readouterr().err


class FakeReader:
    def __init__(self,dsn, *, inspection=None):
        self.dsn=dsn
        self.attested=0
        self.inspection=inspection or lease()
        self.requested_ids=[]

    def assert_heartbeat_monitor_only(self):
        self.attested+=1

    def inspect(self, *, expected_worker_id):
        self.requested_ids.append(expected_worker_id)
        return self.inspection


def config(monkeypatch):
    monkeypatch.setenv("PAPER_JOURNAL_DSN","postgresql://SECRET_PASS@invalid/neondb")
    monkeypatch.setenv("PAPER_EXPECTED_WORKER_ID","paper-worker-2026-a")


def test_entrypoint_healthy_observer_sends_no_alerts_and_never_orders(
        monkeypatch,capsys):
    config(monkeypatch)
    clients=[]
    def reader(dsn):
        c=FakeReader(dsn)
        clients.append(c)
        return c
    delivered=[]
    rc=mod.main(
        ["--send-critical-alert"],
        broker_report=flat_readonly_report,
        monitor_factory=reader,
        send_alert=lambda *args,**kwargs: delivered.append(1))
    assert rc==0
    assert delivered==[]
    assert clients[0].attested==1
    assert clients[0].requested_ids==["paper-worker-2026-a"]
    captured=capsys.readouterr()
    assert "ZERO orders" in captured.out
    assert "SECRET_PASS" not in str(captured)


def test_stale_lease_alerts_with_opt_in_secret_only(monkeypatch,capsys):
    config(monkeypatch)
    monkeypatch.setenv("PAPER_MONITOR_NTFY_TOPIC","x"*32)
    sent=[]
    def send(incident, *, topic):
        sent.append((incident.level,incident.code,topic))
        return True
    rc=mod.main(
        ["--send-critical-alert"], broker_report=flat_readonly_report,
        monitor_factory=lambda d:FakeReader(d,inspection=lease(healthy=False)),
        send_alert=send)
    assert rc==3
    assert sent==[("critical","supervisor_heartbeat_missing","x"*32)]
    assert "x"*32 not in str(capsys.readouterr())


def test_no_topic_or_notification_failure_cannot_be_reported_as_sent(
        monkeypatch,capsys):
    config(monkeypatch)
    monkeypatch.delenv("PAPER_MONITOR_NTFY_TOPIC",raising=False)
    kwargs={
        "broker_report":flat_readonly_report,
        "monitor_factory":lambda d:FakeReader(d,inspection=lease(healthy=False)),
    }
    assert mod.main(["--send-critical-alert"],**kwargs)==2
    assert "ALERT BLOCKED" in capsys.readouterr().err
    monkeypatch.setenv("PAPER_MONITOR_NTFY_TOPIC","x"*32)
    assert mod.main(
        ["--send-critical-alert"], **kwargs,
        send_alert=lambda *args, **kw: False)==2
    assert "ALERT DELIVERY UNVERIFIED" in capsys.readouterr().err


def test_no_alert_flag_does_not_trigger_network(monkeypatch,capsys):
    config(monkeypatch)
    sent=[]
    rc=mod.main(
        [],broker_report=flat_readonly_report,
        monitor_factory=lambda d:FakeReader(d,inspection=lease(healthy=False)),
        send_alert=lambda *a,**k:sent.append(1))
    assert rc==3
    assert sent==[]
    assert "NO trade permission" in capsys.readouterr().err


def test_reader_permission_denial_fails_closed_without_secret_output(monkeypatch,capsys):
    config(monkeypatch)
    class BadReader(FakeReader):
        def assert_heartbeat_monitor_only(self):
            raise RuntimeError("SECRET_NEON_DSN")
    assert mod.main([],broker_report=flat_readonly_report,
                    monitor_factory=BadReader)==3
    captured=capsys.readouterr()
    assert "SECRET_NEON_DSN" not in str(captured)


def test_module_contains_no_order_writer():
    s=Path(mod.__file__).read_text()
    assert "submit_order(" not in s
    assert "cancel_order(" not in s
    assert "TradingClient(" not in s
    assert "UPDATE public.paper_control" not in s
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = True" not in s
