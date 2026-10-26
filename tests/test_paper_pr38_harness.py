"""Offline checks for the manual real-Neon PR #38 concurrency harness."""
import os
from pathlib import Path
import subprocess
import sys
import importlib.util

import pytest

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
    assert "urlsplit(dsn)" in src
    assert "verify_safe_controls(owner_dsn)" in src
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


def _harness():
    spec = importlib.util.spec_from_file_location("hds_pr38_test_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("sqlstate,expected_denial", [
    ("42501", True), ("23505", True), ("42P01", False),
    ("55P03", False), (None, False),
])
def test_only_explicit_expected_denial_codes_count_as_safe(sqlstate, expected_denial,
                                                           monkeypatch):
    h = _harness()

    class FakeSqlError(Exception):
        pass

    class FakeConnection:
        closed = False
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def cursor(self):
            return self
        def execute(self, *_args):
            err = FakeSqlError("REDACTED")
            err.sqlstate = sqlstate
            raise err
        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(h, "connect", lambda *_args: (connection, 404))
    if expected_denial:
        result = h.attempt("fake-test-only-dsn", h.BASE, "F")
        assert result["state"] == sqlstate
        assert result["accepted"] is False
    else:
        with pytest.raises(h.SafetyBlock, match="Unexpected reservation failure"):
            h.attempt("fake-test-only-dsn", h.BASE, "F")
    assert connection.closed is True


def test_preexisting_synthetic_fixture_is_never_deleted(monkeypatch, capsys):
    h = _harness()
    monkeypatch.setenv("HDS_PR38_TEST_OWNER_DSN", "fake-owner")
    monkeypatch.setenv("HDS_PR38_TEST_EXECUTOR_DSN", "fake-executor")
    switches = []
    monkeypatch.setattr(h, "inspect_role", lambda *_args: None)
    monkeypatch.setattr(h, "controls",
                        lambda *_args, enabled: switches.append(enabled))
    monkeypatch.setattr(h, "verify_safe_controls", lambda *_args: None)
    def already_exists(*_args):
        raise h.SafetyBlock("Preexisting fixture, cleanup must not run")
    monkeypatch.setattr(h, "verify_empty_fixtures", already_exists)
    def forbidden_cleanup(*_args):
        raise AssertionError("Preexisting test data would be deleted")
    monkeypatch.setattr(h, "cleanup", forbidden_cleanup)
    assert h.main() == 1
    assert switches == [False, False]
    assert "PASS: real Neon concurrency tests and cleanup completed" not in capsys.readouterr().out


def test_cleanup_failure_cannot_report_acceptance_success(monkeypatch, capsys):
    h = _harness()
    monkeypatch.setenv("HDS_PR38_TEST_OWNER_DSN", "fake-owner")
    monkeypatch.setenv("HDS_PR38_TEST_EXECUTOR_DSN", "fake-executor")
    monkeypatch.setattr(h, "inspect_role", lambda *_args: None)
    monkeypatch.setattr(h, "controls", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(h, "verify_safe_controls", lambda *_args: None)
    monkeypatch.setattr(h, "verify_empty_fixtures", lambda *_args: None)
    monkeypatch.setattr(h, "assert_count", lambda *_args: None)
    monkeypatch.setattr(h, "test_raw_insert_denied", lambda *_args: None)
    monkeypatch.setattr(h, "test_rollback", lambda *_args: None)
    monkeypatch.setattr(h, "test_guard_race",
                        lambda *_args: {"lock_timeout_sqlstate": "55P03"})
    monkeypatch.setattr(h, "test_switch_first", lambda *_args: None)
    def synthetic_parallel(_dsn, _day, symbols):
        if len(symbols) == 4:
            return ([{"pid": 1, "accepted": True, "state": "reserved"}]
                    + [{"pid": i, "accepted": False, "state": "23505"}
                       for i in (2, 3, 4)])
        if symbols == ["F", "BAC"]:
            return [{"pid": i, "accepted": False, "state": "42501"}
                    for i in (1, 2)]
        return [{"pid": 1, "accepted": True, "state": "reserved"},
                {"pid": 2, "accepted": False, "state": "23505"}]
    monkeypatch.setattr(h, "parallel", synthetic_parallel)
    def fail_cleanup(*_args):
        raise h.SafetyBlock("Cleanup failed")
    monkeypatch.setattr(h, "cleanup", fail_cleanup)
    assert h.main() == 1
    output = capsys.readouterr().out
    assert "BLOCKED: could not verify synthetic fixture cleanup" in output
    assert "PASS: real Neon concurrency tests and cleanup completed" not in output


@pytest.mark.parametrize("dsn", [
    "", "not-a-postgres-dsn",
    "postgresql://test-user:test-password@example-pooler.invalid/neondb",
])
def test_live_harness_rejects_missing_or_pooled_neon_endpoint(dsn):
    h = _harness()
    with pytest.raises(h.SafetyBlock, match="direct test-branch"):
        h.connect(dsn, h.EXECUTOR_ROLE)
