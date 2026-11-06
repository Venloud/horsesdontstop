"""Offline source-contract checks for the bounded PAPER executor launcher."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "ops/windows/run-paper-executor.ps1"
SUPERVISOR_RUNNER = ROOT / "ops/windows/run-paper-supervisor.ps1"


def test_monitor_password_is_assembled_into_the_least_privilege_journal_dsn():
    code = RUNNER.read_text(encoding="utf-8")
    assert "$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'" in code
    assert "[Uri]::EscapeDataString(" in code
    assert "Read-HdsExecutorSecret 'neon-monitor.dpapi'" in code
    assert 'postgresql://hds_paper_win_monitor:${monitorPassword}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require' in code
    assert "$env:PAPER_JOURNAL_DSN = Read-HdsExecutorSecret" not in code
    assert "Remove-Variable monitorPassword" in code


def test_launcher_runs_prepare_by_default_and_cleans_environment():
    code = RUNNER.read_text(encoding="utf-8")
    assert "--prepare --output $output" in code
    assert "--dispatch-file $ProposalPath --output $output" in code
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED" not in code
    assert 'Remove-Item "Env:$name"' in code
    assert "no secret values displayed" in code


def test_launcher_records_specific_sanitized_failure_stage():
    code = RUNNER.read_text(encoding="utf-8")
    assert "$receipt = Join-Path $folder 'executor-runtime.json'" in code
    assert "function Write-HdsRuntimeReceipt" in code
    assert "stage = $stage" in code
    assert "error_type = $errorType" in code
    assert "details withheld to protect credentials" in code
    assert "--runtime-receipt $receipt" in code
    assert "$_ .Exception.Message" not in code
    assert "Write-Host 'PAPER EXECUTOR BLOCKED: required protected configuration or runtime evidence unavailable.'" not in code


def test_launcher_pins_receipt_to_exactly_one_live_supervisor_process():
    code = RUNNER.read_text(encoding="utf-8")
    assert "Get-CimInstance Win32_Process" in code
    assert "-m\\s+bot\\.paper_supervisor_loop" in code
    assert "$supervisorRoots.Count -ne 1" in code
    assert "$env:PAPER_SUPERVISOR_RECEIPT_PID" in code
    assert "$supervisorIds -notcontains $receiptPid" in code


def test_market_diagnostic_uses_the_same_supervisor_pid_and_receipt():
    code = SUPERVISOR_RUNNER.read_text(encoding="utf-8")
    assert 'PAPER_SUPERVISOR_RECEIPT' in code
    assert 'PAPER_SUPERVISOR_RECEIPT_PID' in code
    assert '$script:HdsLastServiceRootPids' in code
    assert '$script:HdsLastServiceProcessIds' in code
    assert '$supervisorCount -eq 1 -and' in code
    assert 'bot.paper_supervisor_loop' in code
