"""Static assertions for the existing Windows PAPER read-only launcher.

These tests do not start PowerShell, decrypt credentials, or call Alpaca.
"""
from pathlib import Path

RUNNER = (Path(__file__).resolve().parents[1] /
          "ops" / "windows" / "run-paper-supervisor.ps1")


def test_safe_bounded_supervisor_default_and_explicit_opt_in_continuous():
    code = RUNNER.read_text(encoding="utf-8")
    assert "[int]$MaxTicks = 3" in code
    assert "[switch]$Continuous" in code
    assert "$runTicks = if ($Continuous) { 0 } else { $MaxTicks }" in code
    assert "--max-ticks $runTicks" in code
    assert "--external-liveness" in code
    assert "--interval-seconds 30" in code


def test_no_broker_writes_or_trading_release_in_runner():
    code = RUNNER.read_text(encoding="utf-8")
    for term in ("submit_order", "cancel_order", "replace_order",
                 "close_position", "PAPER_ORDER_SUBMISSIONS_RELEASED",
                 "submissions_enabled = true"):
        assert term not in code
    assert 'Read-HdsSecret "alpaca-paper-key.dpapi"' in code
    assert 'Read-HdsSecret "alpaca-paper-secret.dpapi"' in code
    assert 'Read-HdsSecret "beat-token.dpapi"' in code
    assert 'Remove-Item "Env:$name"' in code


def test_supervisor_exit_status_reaches_task_scheduler_after_cleanup():
    code = RUNNER.read_text(encoding="utf-8")
    assert "$supervisorExitCode = $LASTEXITCODE" in code
    assert "Write-Host \"SUPERVISOR EXIT CODE: $supervisorExitCode\"" in code
    assert 'Remove-Item "Env:$name"' in code
    # Preserve the native status across the outer try/finally cleanup,
    # and fail closed if Python produces no exit code.
    cleanup_pos = code.rfind('Remove-Item "Env:$name"')
    final_exit_pos = code.rfind("exit $supervisorExitCode")
    assert final_exit_pos > cleanup_pos
    assert "if ($null -eq $supervisorExitCode) { exit 2 }" in code
