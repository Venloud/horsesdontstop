"""Adversarial offline tests; no credentials, migrations or broker writes."""
from pathlib import Path

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


def test_cutover_covers_column_and_inherited_write_bypasses():
    source = (ROOT / "sql/paper_status_executor_cutover_review_only.sql").read_text()
    assert "has_any_column_privilege(r.role_name,t.tbl,'INSERT,UPDATE,REFERENCES')" in source
    assert "REVOKE INSERT (%s), UPDATE (%s), REFERENCES (%s)" in source
    assert "Inherited, PUBLIC or column journal write bypass; rollback" in source
