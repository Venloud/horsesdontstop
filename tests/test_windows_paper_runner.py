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


INSTALLER = RUNNER.with_name("install-paper-monitoring-tasks.ps1")


def test_diagnose_mode_counts_services_logs_and_dedups_alerts():
    code = RUNNER.read_text(encoding="utf-8")
    assert 'Get-HdsServiceInstances "bot.paper_opening_equity_daemon"' in code
    assert 'Get-HdsServiceInstances "bot.paper_supervisor_loop"' in code
    assert "--collector-instances $collectorCount" in code
    assert "--supervisor-instances $supervisorCount" in code
    assert '"diagnostic-log.jsonl"' in code
    assert '"diagnostic-alert-state.json"' in code
    # A failed process count is -1, which the diagnostic treats as FAIL.
    assert "return -1" in code
    # The diagnostic's own process must not count as a supervisor.
    assert '-notmatch "--diagnose"' in code


def test_continuous_supervisor_uses_external_liveness_and_neon_idle():
    code = RUNNER.read_text(encoding="utf-8")
    assert ("--max-ticks 0 --interval-seconds 30 --external-liveness "
            "--idle-neon-when-broker-closed-flat") in code


def test_monitoring_installer_reuses_names_and_single_instances():
    code = INSTALLER.read_text(encoding="utf-8")
    assert code.count("Register-ScheduledTask") == 2
    assert '"HorsesDontStop-PAPER-Supervisor"' in code
    assert '"HorsesDontStop-PAPER-Diagnostic"' in code
    assert "HorsesDontStop-PAPER-OpeningEquity" not in code.split("Register-ScheduledTask")[1]
    assert code.count("-MultipleInstances IgnoreNew") == 2
    assert '-At "09:21"' in code and '-At "09:36"' in code
    assert "-LogonType Interactive" in code and "-RunLevel Limited" in code
    for term in ("submit_order", "cancel_order", "replace_order",
                 "PAPER_ORDER_SUBMISSIONS_RELEASED", "Unregister-ScheduledTask"):
        assert term not in code
