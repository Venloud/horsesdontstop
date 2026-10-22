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
    code = "NOT_FIRST_BEAT" if failed == "first_heartbeat" else "UNKNOWN"
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
