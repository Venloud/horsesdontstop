from pathlib import Path


SQL = (Path(__file__).resolve().parents[1] / "sql" /
       "paper_dynamic_risk_sizing_review_only.sql").read_text(encoding="utf-8")
ROLLBACK = (Path(__file__).resolve().parents[1] / "sql" /
            "paper_dynamic_risk_sizing_rollback_review_only.sql").read_text(encoding="utf-8")


def test_dynamic_migration_removes_only_fixed_notional_ceiling():
    assert "DROP CONSTRAINT IF EXISTS paper_pilot_approvals_purchase_cost_check" in SQL
    assert "CHECK (purchase_cost > 0)" in SQL
    assert "p_expected_entry * p_whole_shares > 5.00" not in SQL
    assert "v_cost > 5.00" not in SQL
    assert "PAPER_MAX_PURCHASE" not in SQL
    assert "DROP CONSTRAINT IF EXISTS paper_pilot_approvals_fee_reserve_check" not in SQL
    assert "fee_reserve IS DISTINCT FROM 0.50" in SQL
    assert "kill ON and submissions OFF" in SQL


def test_exact_owner_approval_and_single_use_dispatch_boundaries_remain():
    assert "SECURITY DEFINER SET search_path = pg_catalog" in SQL
    assert "SECURITY INVOKER SET search_path = pg_catalog" in SQL
    assert "OWNER TO hds_paper_reservation_guard" in SQL
    assert "OWNER TO neondb_owner" in SQL
    assert "FROM PUBLIC, hds_paper_executor, hds_paper_reconciler" in SQL
    assert "TO hds_paper_executor" in SQL
    assert "v_approval.purchase_cost IS DISTINCT FROM" in SQL
    assert "v_approval.fee_reserve IS DISTINCT FROM 0.50" in SQL
    assert "INSERT INTO public.paper_pilot_dispatch_receipts" in SQL
    assert "SET consumed_at=clock_timestamp()" in SQL
    assert "paper_mark_reserved_intent_unknown" in SQL
    assert "Earlier PAPER order requires reconciliation" in SQL


def test_rollback_refuses_to_restore_cap_when_larger_orders_are_recorded():
    assert "purchase_cost > 5.00" in ROLLBACK
    assert "expected_entry * whole_shares > 5.00" in ROLLBACK
    assert "status IN ('reserved','submitted','accepted','unknown')" in ROLLBACK
    assert "paper_pilot_approvals" in ROLLBACK
