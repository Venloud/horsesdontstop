"""Offline source-contract checks for staged Windows PAPER equity hosting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_collector_reuses_existing_daemon_and_current_user_dpapi():
    runner = (ROOT / "ops/windows/run-paper-opening-equity.ps1").read_text()
    assert "-m bot.paper_opening_equity_daemon --interval-seconds 15" in runner
    assert "neon-executor-dsn.dpapi" in runner
    assert "ConvertTo-SecureString" in runner
    assert "Remove-Item \"Env:$name\"" in runner
    assert "paper_supervisor_loop" not in runner
    for forbidden in ("submit_order", "cancel_order", "replace_order",
                      "PAPER_ORDER_SUBMISSIONS_RELEASED"):
        assert forbidden not in runner


def test_task_does_not_replace_supervisor_or_embed_secrets():
    installer = (ROOT / "ops/windows/install-paper-opening-equity-task.ps1").read_text()
    assert "HorsesDontStop-PAPER-OpeningEquity" in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" in installer
    assert "New-ScheduledTaskTrigger -Weekly" in installer
    assert "09:20" in installer
    assert "-RestartCount 3" in installer
    assert "-LogonType Interactive" in installer
    assert "run-paper-opening-equity.ps1" in installer
    assert "run-paper-supervisor.ps1" not in installer
    assert "PAPER_EXECUTOR_DSN" not in installer


def test_setup_prompts_hidden_and_refuses_overwrite():
    setup = (ROOT / "ops/windows/set-paper-equity-executor-credential.ps1").read_text()
    assert "Read-Host -AsSecureString" in setup
    assert "ConvertFrom-SecureString" in setup
    assert "Refusing silent overwrite" in setup


def test_sampler_remains_narrow_and_never_invents_late_baseline():
    sample = (ROOT / "bot/paper_equity_open.py").read_text()
    journal = (ROOT / "bot/paper_execution_journal.py").read_text()
    assert "opening_window(after_read_now" in sample
    assert "start <= db_now < end" in journal
    assert "assert_executor_permissions()" in journal
