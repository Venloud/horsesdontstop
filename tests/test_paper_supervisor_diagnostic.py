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
    stages["first_heartbeat"] = lambda: "EXPECTED_FIRST_BEAT"
    assert run_diagnostic_stages(stages) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(DIAGNOSTIC_STAGES) == 12
    assert lines[-1] == "STAGE first_heartbeat: EXPECTED_FIRST_BEAT"
    assert [x.split(":")[0] for x in lines] == [
        "STAGE " + name for name in DIAGNOSTIC_STAGES
    ]


@pytest.mark.parametrize("failed", DIAGNOSTIC_STAGES)
def test_exactly_one_stage_fails_and_all_others_run(failed, capsys):
    stages = good_stages()
    reasons = {
        "required_env": "MISSING_ENV",
        "alpaca_account": "AUTH_REJECTED",
        "alpaca_positions_orders": "TIMEOUT",
        "neon_journal": "PERMISSION_DENIED",
        "neon_control": "SCHEMA_MISMATCH",
        "observation_freshness": "STALE",
        "reconciliation": "BLOCKERS_PRESENT",
        "independent_assessment": "BLOCKERS_PRESENT",
        "heartbeat": "PERMISSION_DENIED",
        "external_liveness": "HTTP_4XX",
        "nyse_calendar": "MARKET_CLOSED",
        "first_heartbeat": "NOT_FIRST_BEAT",
    }
    code = reasons[failed]
    def fail():
        raise DiagnosticFailure(code)
    stages[failed] = fail
    assert run_diagnostic_stages(stages) == 2
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 12
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
    stages["heartbeat"] = lambda: False
    assert run_diagnostic_stages(stages) == 2
    assert "STAGE heartbeat: FAIL UNKNOWN" in capsys.readouterr().out

def test_real_diagnostic_entrypoint_resolves_imports_without_external_io(monkeypatch):
    """Catch missing imports in diagnose_supervisor, not just fake stage runner."""
    import bot.paper_supervisor_loop as module
    seen = []
    def collect(stages):
        seen.extend(stages)
        return 0
    monkeypatch.setattr(module, "run_diagnostic_stages", collect)
    assert module.diagnose_supervisor() == 0
    assert tuple(seen) == DIAGNOSTIC_STAGES
