"""Offline checks for the manual real-Neon PR #38 concurrency harness."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "ops" / "paper_pr38_neon_concurrency.py"


def test_manual_neon_harness_compiles_and_has_branch_identity_gate():
    src = HARNESS.read_text(encoding="utf-8")
    compile(src, str(HARNESS), "exec")
    assert 'TARGET = "br-rapid-glitter-b53xh7rb"' in src
    assert "public.hds_pr38_test_marker" in src
    assert "Barrier(" in src
    assert "ThreadPoolExecutor(" in src
    assert "has_table_privilege(current_user,'public.paper_intents','INSERT')" in src
    assert "sslmode=" in src
    assert 'controls(owner_dsn, enabled=False)' in src
    assert "cleanup(owner_dsn, keys)" in src
    assert "TradingClient(" not in src
    assert "submit_order(" not in src


def test_manual_harness_does_not_open_connections_without_both_dsns():
    env = os.environ.copy()
    env.pop("HDS_PR38_TEST_OWNER_DSN", None)
    env.pop("HDS_PR38_TEST_EXECUTOR_DSN", None)
    p = subprocess.run(
        [sys.executable, str(HARNESS)], env=env,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert p.returncode == 2
    assert "BLOCKED" in p.stdout
    assert "postgresql://" not in p.stdout + p.stderr
