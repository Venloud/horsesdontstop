from pathlib import Path


SQL = (Path(__file__).resolve().parents[1] / "sql" /
       "paper_bounded_pilot_dispatch_review_only.sql").read_text(encoding="utf-8")


def test_pilot_migration_binds_gtc_terms_and_hard_purchase_fee_caps():
    assert "ADD COLUMN IF NOT EXISTS time_in_force" in SQL
    assert "p_expected_entry * p_whole_shares > 5.00" in SQL
    assert "kill switch ON and submissions OFF" in SQL
    assert "fee_reserve = 0.50" in SQL
    assert "i.time_in_force='gtc'" in SQL
    assert "v_cost > 5.00" in SQL
    assert "GTC OTO" in SQL


def test_exact_owner_approval_is_single_use_and_hash_bound():
    assert "paper_approve_bounded_pilot" in SQL
    assert "request_sha256" in SQL
    assert "account_id_sha256" in SQL
    assert "expires_at <= approved_at + interval '120 seconds'" in SQL
    assert "consumed_at IS NOT NULL" in SQL
    assert "SET consumed_at=clock_timestamp()" in SQL
    assert "client_order_id text PRIMARY KEY" in SQL


def test_dispatch_receipt_and_unknown_status_commit_before_the_post():
    assert "paper_commit_bounded_pilot_dispatch" in SQL
    assert "INSERT INTO public.paper_pilot_dispatch_receipts" in SQL
    assert "paper_mark_reserved_intent_unknown" in SQL
    assert "Earlier PAPER order requires reconciliation" in SQL
    assert "commits before POST" in SQL


def test_executor_cannot_write_approval_or_dispatch_tables_directly():
    assert "FROM PUBLIC, hds_paper_executor" in SQL
    assert "TO hds_paper_executor;" in SQL
    assert "GRANT EXECUTE ON FUNCTION public.paper_commit_bounded_pilot_dispatch" in SQL
    assert "GRANT EXECUTE ON FUNCTION public.paper_approve_bounded_pilot" not in SQL
    assert "GRANT INSERT, UPDATE ON public.paper_pilot_approvals TO neondb_owner" not in SQL
    assert "REVOKE EXECUTE ON FUNCTION public.paper_authorize_unknown_submission" in SQL

