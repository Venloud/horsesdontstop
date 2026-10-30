"""Offline stage fault isolation, no network or broker clients constructed."""
import pytest

from bot.paper_supervisor_loop import (
    DIAGNOSTIC_STAGES, DIAGNOSTIC_REASONS, DiagnosticFailure,
    run_diagnostic_stages,
)


def good_stages():
    return {name: (lambda: True) for name in DIAGNOSTIC_STAGES}


def test_all_stages_pass_and_order_is_fixed(capsys):
    stages = good_stages()
    assert run_diagnostic_stages(stages) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(DIAGNOSTIC_STAGES) == 14
    assert lines[-1] == "STAGE continuous_supervisor_lease: PASS"
    assert [x.split(":")[0] for x in lines] == [
        "STAGE " + name for name in DIAGNOSTIC_STAGES
    ]


@pytest.mark.parametrize("failed", DIAGNOSTIC_STAGES)
def test_exactly_one_stage_fails_and_all_others_run(failed, capsys):
    stages = good_stages()
    reasons = {
        "required_env": "MISSING_ENV",
        "opening_equity_collector": "PROCESS_NOT_RUNNING",
        "continuous_supervisor_process": "DUPLICATE_PROCESS",
        "alpaca_account": "AUTH_REJECTED",
        "alpaca_positions_orders": "TIMEOUT",
        "neon_journal": "PERMISSION_DENIED",
        "neon_control": "SCHEMA_MISMATCH",
        "observation_freshness": "STALE",
        "reconciliation": "BLOCKERS_PRESENT",
        "independent_assessment": "BLOCKERS_PRESENT",
        "heartbeat_writer_role": "PERMISSION_DENIED",
        "external_liveness_config": "UNKNOWN",
        "nyse_calendar": "MARKET_CLOSED",
        "continuous_supervisor_lease": "SUPERVISOR_NOT_RUNNING",
    }
    code = reasons[failed]
    def fail():
        raise DiagnosticFailure(code)
    stages[failed] = fail
    assert run_diagnostic_stages(stages) == 2
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 14
    assert [line for line in lines if ": FAIL " in line] == [
        "STAGE " + failed + ": FAIL " + code
    ]


@pytest.mark.parametrize("code", sorted(DIAGNOSTIC_REASONS))
def test_reason_allowlist(code, capsys):
    stages = good_stages()
    stages["neon_control"] = lambda: code
    assert run_diagnostic_stages(stages) == 2
    assert "STAGE neon_control: FAIL " + code in capsys.readouterr().out


def test_secret_never_reaches_console(capsys):
    stages = good_stages()
    def leak():
        raise RuntimeError("VERY_SECRET_KEY postgres://admin:password@host")
    stages["alpaca_account"] = leak
    assert run_diagnostic_stages(stages) == 2
    output = capsys.readouterr()
    assert "VERY_SECRET_KEY" not in output.out + output.err
    assert "password" not in output.out + output.err
    assert "STAGE alpaca_account: FAIL UNKNOWN" in output.out


def test_unknown_return_never_passes(capsys):
    stages = good_stages()
    stages["heartbeat_writer_role"] = lambda: False
    assert run_diagnostic_stages(stages) == 2
    assert "STAGE heartbeat_writer_role: FAIL UNKNOWN" in capsys.readouterr().out

def test_real_diagnostic_entrypoint_resolves_imports_without_external_io(monkeypatch):
    """Catch missing imports in diagnose_supervisor, not just fake stage runner."""
    import bot.paper_supervisor_loop as module
    seen = []
    def collect(stages, **kw):
        seen.extend(kw["names"])
        assert set(kw["names"]) <= set(stages)
        return 0
    monkeypatch.setattr(module, "run_diagnostic_stages", collect)
    assert module.diagnose_supervisor() == 0
    assert tuple(seen) == DIAGNOSTIC_STAGES
    seen.clear()
    assert module.diagnose_supervisor(phase="preopen") == 0
    assert tuple(seen) == module.PREOPEN_DIAGNOSTIC_STAGES

def test_assessor_codes_are_not_raw_exception_messages(capsys):
    stages = good_stages()
    stages["independent_assessment"] = lambda: "MISSING_OPENING_EQUITY"
    stages["continuous_supervisor_lease"] = lambda: "UNHEALTHY_OBSERVATION"
    assert run_diagnostic_stages(stages) == 2
    out = capsys.readouterr().out
    assert "STAGE independent_assessment: FAIL MISSING_OPENING_EQUITY" in out
    assert "STAGE continuous_supervisor_lease: FAIL UNHEALTHY_OBSERVATION" in out

@pytest.mark.parametrize(("incident_code", "expected"), [
    ("missing_opening_equity", "MISSING_OPENING_EQUITY"),
    ("unsafe_exposure", "UNSAFE_EXPOSURE"),
    ("degraded", "DEGRADED"),
    ("security_switch", "SECURITY_SWITCH"),
    ("unexpected_broker_write", "UNEXPECTED_BROKER_WRITE"),
    ("stale_observation", "STALE"),
    ("idle", "FINGERPRINT_MISMATCH"),
    ("SENSITIVE_UNTRUSTED_VALUE", "BLOCKERS_PRESENT"),
])
def test_independent_assessment_maps_real_incident_safely(monkeypatch, incident_code, expected):
    from types import SimpleNamespace
    import bot.paper_supervisor_loop as module
    monkeypatch.setattr(module, "valid_observation", lambda report: False)
    monkeypatch.setattr(module, "assess_read_only_report", lambda report, **kw:
                        SimpleNamespace(code=incident_code,
                                        level="idle" if incident_code == "idle" else "critical"))
    assert module.diagnostic_assessment_result({"private": "DO_NOT_PRINT"}) == expected


# ---- 2026-10-23: read-only diagnostic, process/lease checks, log, alerts ----
import inspect
import json
from datetime import datetime, timedelta, timezone

from bot import paper_supervisor_loop as loop

NOW = datetime(2026, 10, 26, 13, 36, tzinfo=timezone.utc)  # 09:36 New York


def test_diagnostic_never_writes_lease_or_liveness():
    # The diagnostic must not create a heartbeat lease that later looks
    # stale, nor post a Cloudflare beat that pages the phone afterwards.
    source = inspect.getsource(loop.diagnose_supervisor)
    assert ".pulse(" not in source
    assert "send_liveness" not in source
    assert "INSERT" not in source.upper().replace("INSERTED", "")
    assert "UPDATE" not in source.upper()


def test_preopen_stage_set_is_a_strict_dependency_subset():
    assert set(loop.PREOPEN_DIAGNOSTIC_STAGES) < set(DIAGNOSTIC_STAGES)
    for later in ("observation_freshness", "independent_assessment",
                  "continuous_supervisor_lease", "reconciliation"):
        assert later not in loop.PREOPEN_DIAGNOSTIC_STAGES


def test_preopen_run_prints_only_preopen_stages(capsys):
    assert run_diagnostic_stages(good_stages(),
                                 names=loop.PREOPEN_DIAGNOSTIC_STAGES) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(loop.PREOPEN_DIAGNOSTIC_STAGES)


@pytest.mark.parametrize(("when", "expected"), [
    (datetime(2026, 10, 26, 13, 21, tzinfo=timezone.utc), "preopen"),
    (datetime(2026, 10, 26, 13, 29, 59, tzinfo=timezone.utc), "preopen"),
    (datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc), "postopen"),
    (datetime(2026, 12, 7, 14, 21, tzinfo=timezone.utc), "preopen"),   # EST
    (datetime(2026, 12, 7, 14, 36, tzinfo=timezone.utc), "postopen"),
])
def test_auto_phase_uses_new_york_time_across_dst(when, expected):
    assert loop.diagnostic_phase("auto", when) == expected


@pytest.mark.parametrize(("count", "expected"), [
    (1, True), (0, "PROCESS_NOT_RUNNING"), (2, "DUPLICATE_PROCESS"),
    (-1, "PROCESS_UNVERIFIED"), (None, "PROCESS_UNVERIFIED"),
    (True, "PROCESS_UNVERIFIED"), ("1", "PROCESS_UNVERIFIED"),
])
def test_process_count_fails_closed(count, expected):
    assert loop.diagnostic_process_count(count) == expected


@pytest.mark.parametrize(("row", "expected"), [
    (None, "SUPERVISOR_NOT_RUNNING"),
    ((NOW - timedelta(seconds=30), True, NOW), True),
    ((NOW - timedelta(seconds=30), False, NOW), "UNHEALTHY_OBSERVATION"),
    ((NOW - timedelta(seconds=121), True, NOW), "LEASE_STALE"),
    ((NOW + timedelta(seconds=5), True, NOW), "LEASE_UNVERIFIED"),
    ((NOW.replace(tzinfo=None), True, NOW), "LEASE_UNVERIFIED"),
    ((NOW, "true", NOW), "LEASE_UNVERIFIED"),
    ((NOW,), "LEASE_UNVERIFIED"),
])
def test_supervisor_lease_is_read_and_classified_strictly(row, expected):
    assert loop.diagnostic_supervisor_lease(row) == expected


def test_alert_is_sent_once_per_unchanged_failure_per_day(tmp_path):
    sent = []
    def transport(incident, *, topic, details):
        sent.append((incident.code, details))
        return True
    state = tmp_path / "state.json"
    results = {"required_env": "PASS",
               "independent_assessment": "FAIL MISSING_OPENING_EQUITY"}
    args = dict(phase="postopen", topic="t" * 30, state_path=state,
                transport=transport)
    assert loop.alert_diagnostic_failure(results, now=NOW, **args) == "SENT"
    assert loop.alert_diagnostic_failure(results, now=NOW, **args) == "SUPPRESSED_DUPLICATE"
    assert sent == [("diagnostic_failed",
                     "independent_assessment:MISSING_OPENING_EQUITY")]
    # A different failure set is a new incident.
    changed = dict(results, nyse_calendar="FAIL MARKET_CLOSED")
    assert loop.alert_diagnostic_failure(changed, now=NOW, **args) == "SENT"
    # Next trading day, the same failure pages again.
    assert loop.alert_diagnostic_failure(
        results, now=NOW + timedelta(days=1), **args) == "SENT"
    assert len(sent) == 3


def test_failed_delivery_is_not_recorded_and_retries(tmp_path):
    state = tmp_path / "state.json"
    def broken(incident, *, topic, details):
        raise RuntimeError("https://ntfy.sh/SECRET_TOPIC down")
    results = {"neon_journal": "FAIL TIMEOUT"}
    with pytest.raises(RuntimeError):
        loop.alert_diagnostic_failure(results, phase="postopen", topic="t" * 30,
                                      state_path=state, now=NOW, transport=broken)
    assert not state.exists()


def test_unacknowledged_delivery_never_suppresses_future_failure(tmp_path):
    state = tmp_path / "state.json"
    results = {"neon_journal": "FAIL TIMEOUT"}
    with pytest.raises(RuntimeError, match="unacknowledged"):
        loop.alert_diagnostic_failure(
            results, phase="postopen", topic="t" * 30, state_path=state,
            now=NOW, transport=lambda *_a, **_k: False)
    assert not state.exists()
    received = []
    def accepted(*_a, **_k):
        received.append(True)
        return True
    assert loop.alert_diagnostic_failure(
        results, phase="postopen", topic="t" * 30, state_path=state,
        now=NOW, transport=accepted) == "SENT"
    assert received == [True]


def test_test_alert_does_not_report_success_on_unacknowledged_transport(monkeypatch, capsys):
    from types import SimpleNamespace
    from bot import monitor_notify
    monkeypatch.setenv("PAPER_MONITOR_NTFY_TOPIC", "t" * 30)
    monkeypatch.setattr(monitor_notify, "send_critical_ntfy",
                        lambda *_a, **_kw: False)
    args = SimpleNamespace(test_alert=True)
    assert loop.run_diagnostic_cli(args) == 2
    assert "TEST ALERT: DELIVERY FAILED" in capsys.readouterr().out


def test_log_is_fixed_tokens_and_rotates(tmp_path):
    log = tmp_path / "diag.jsonl"
    record = {"at_utc": NOW.isoformat(), "phase": "postopen", "exit_code": 2,
              "alert": "SENT", "stages": {"neon_journal": "FAIL TIMEOUT"}}
    for _ in range(3):
        loop.write_diagnostic_log(log, record, max_bytes=150)
    assert (tmp_path / "diag.jsonl.1").exists()
    assert json.loads(log.read_text().splitlines()[-1]) == record


def test_cli_end_to_end_logs_alerts_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    calls = []
    def fake_diagnose(**kw):
        kw["results"].update({"required_env": "PASS",
                              "opening_equity_collector": "FAIL PROCESS_NOT_RUNNING"})
        calls.append(kw)
        return 2
    monkeypatch.setattr(loop, "diagnose_supervisor", fake_diagnose)
    monkeypatch.setattr(loop, "alert_diagnostic_failure",
                        lambda results, **kw: "SENT")
    monkeypatch.setenv("PAPER_MONITOR_NTFY_TOPIC", "a" * 30)
    args = SimpleNamespace(test_alert=False, phase="preopen", collector_instances=0,
                           supervisor_instances=1, log_file=str(tmp_path / "l.jsonl"),
                           alert_state=str(tmp_path / "s.json"))
    assert loop.run_diagnostic_cli(args) == 2
    assert calls[0]["phase"] == "preopen" and calls[0]["collector_instances"] == 0
    entry = json.loads((tmp_path / "l.jsonl").read_text())
    assert entry["exit_code"] == 2 and entry["alert"] == "SENT"
    assert entry["stages"]["opening_equity_collector"] == "FAIL PROCESS_NOT_RUNNING"
    assert "DIAGNOSTIC RESULT: FAIL; ALERT: SENT" in capsys.readouterr().out


def test_ntfy_details_only_forward_fixed_tokens():
    from bot.monitor_notify import send_critical_ntfy
    from bot.monitor_policy import make_incident
    bodies = []
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(request, timeout):
        bodies.append(request.data.decode())
        return Resp()
    incident = make_incident("critical", "diagnostic_failed", ("x",))
    send_critical_ntfy(incident, topic="a" * 30, open_url=opener,
                       details="neon_journal:TIMEOUT")
    send_critical_ntfy(incident, topic="a" * 30, open_url=opener,
                       details="postgres://admin:pw@host/db")
    assert "Failed: neon_journal:TIMEOUT" in bodies[0]
    assert "postgres" not in bodies[1] and "Failed:" not in bodies[1]
