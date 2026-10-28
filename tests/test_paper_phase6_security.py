"""Adversarial offline tests; no credentials, migrations or broker writes."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys

import pytest

from bot.paper_role_boundary import assert_journal_acl_boundary
from bot.paper_safety import TradeBlocked

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("result", [(False,), None, (None,), ()])
def test_unverifiable_effective_privileges_fail_closed(result):
    class Cursor:
        def execute(self, sql): self.sql = sql
        def fetchone(self): return result
    cur = Cursor()
    with pytest.raises(TradeBlocked):
        assert_journal_acl_boundary(cur, reconciler=True)
    # This checks the actual effective ACL query, not a role name alone.
    assert "has_any_column_privilege" in cur.sql
    assert "pg_has_role(current_user,c.relowner,'MEMBER')" in cur.sql
    assert "has_function_privilege(current_user,p.oid,'EXECUTE')" in cur.sql
    assert "paper_authorize_unknown_submission" in cur.sql
    assert "session_user = current_user" in cur.sql
    assert "rolcanlogin" in cur.sql


def test_final_submission_sql_preserves_lock_terms_and_install_boundary():
    source = (ROOT / "sql/paper_submission_reauthorization_review_only.sql").read_text()
    assert source.startswith("-- REVIEW ONLY.")
    assert source.index("BEGIN;") < source.index("CREATE FUNCTION") < source.index("COMMIT;")
    assert "FROM public.paper_reservation_control_guard()" in source
    assert "pg_advisory_xact_lock" in source
    assert "i.status='unknown'" in source and "i.broker_order_id IS NULL" in source
    for term in ("strategy", "symbol", "trading_day", "whole_shares", "expected_entry", "protective_stop"):
        assert f"i.{term}=p_{term}" in source
    assert "REVOKE ALL ON FUNCTION" in source and "FROM PUBLIC" in source
    assert "SET search_path = pg_catalog" in source
    assert "UPDATE public.paper_control" not in source
    assert "CREATE UNIQUE INDEX uq_paper_submission_authorization_consumed" in source
    assert "WHERE kind='submission_authorization_consumed'" in source
    assert "paper_consume_submission_authorization" in source


def test_cutover_covers_column_and_inherited_write_bypasses():
    source = (ROOT / "sql/paper_status_executor_cutover_review_only.sql").read_text()
    assert "has_any_column_privilege(r.role_name,t.tbl,'INSERT,UPDATE,REFERENCES')" in source
    assert "REVOKE INSERT (%s), UPDATE (%s), REFERENCES (%s)" in source
    assert "Inherited, PUBLIC or column journal write bypass; rollback" in source


def test_acceptance_harness_requires_explicit_scope_and_private_credentials():
    path = ROOT / "ops/paper_phase6_acl_acceptance.py"
    compile(path.read_text(), str(path), "exec")
    env = {k: v for k, v in os.environ.items() if not k.startswith("HDS_PHASE6_TEST_")}
    process = subprocess.run([sys.executable, str(path)], env=env,
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 2 and "BLOCKED" in process.stdout
    assert "postgresql://" not in process.stdout + process.stderr
    spec = importlib.util.spec_from_file_location("phase6_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(module.AcceptanceBlocked):
        module.connect("postgresql://neondb_owner:fake@example.neon.tech/neondb",
                       "neondb_owner", module.PRODUCTION)


@pytest.mark.parametrize("state", ["42501", "42P01", "55P03", None])
def test_harness_does_not_confuse_errors_with_security_denials(state):
    path = ROOT / "ops/paper_phase6_acl_acceptance.py"
    spec = importlib.util.spec_from_file_location("phase6_denial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class DBError(Exception): pass
    class Connection:
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, *_):
            err = DBError("SENSITIVE")
            err.sqlstate = state
            raise err
        def rollback(self): self.rolled_back = True
    conn = Connection()
    if state == "42501":
        module.denied(conn, "synthetic attack")
    else:
        with pytest.raises(module.AcceptanceBlocked):
            module.denied(conn, "synthetic attack")
    assert conn.rolled_back
