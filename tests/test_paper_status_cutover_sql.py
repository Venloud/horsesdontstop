"""Static non-execution checks for PR38-dependent status cutover.

Real per-role PostgreSQL privilege/locking tests require fresh OWNER APPROVAL
on an isolated Neon branch; this test never modifies any database.
"""
from pathlib import Path


SQL_FILE = (Path(__file__).resolve().parents[1] /
            "sql" / "paper_status_executor_cutover_review_only.sql")


def sql():
    return SQL_FILE.read_text(encoding="utf-8")


def test_cutover_is_explicitly_not_a_production_installer():
    source = sql()
    assert source.startswith("-- REVIEW ONLY.")
    assert "BEGIN;" in source and "COMMIT;" in source
    assert (source.index("BEGIN;") < source.index("DO $preflight$")
            < source.index("DO $verify$") < source.index("COMMIT;"))
    assert "paper_control SET" not in source
    assert "UPDATE public.paper_control" not in source
    assert "DELETE FROM" not in source
    assert "DROP TABLE" not in source
    assert "GRANT hds_paper_reconciler TO hds_paper_executor" not in source


def test_cannot_skip_pr38_atomic_guard_or_status_prepare():
    source = sql()
    assert "paper_reserve_intent_if_enabled(" in source
    assert "paper_mark_reserved_intent_unknown(text)" in source
    assert "paper_record_reconciled_intent_status(" in source
    assert "uq_paper_intents_trading_day" in source
    assert "NOT has_function_privilege('hds_paper_executor'" in source
    assert "has_table_privilege('hds_paper_executor','public.paper_intents','INSERT')" in source
    assert "has_table_privilege('hds_paper_executor','public.paper_control','UPDATE')" in source
    assert "pg_has_role('hds_paper_executor','hds_paper_reconciler','MEMBER')" in source
    assert "pg_has_role('hds_paper_executor','hds_paper_status_owner','MEMBER')" in source
    assert "pg_has_role('hds_paper_executor','hds_paper_reservation_guard','MEMBER')" in source
    assert "RAISE EXCEPTION 'PR38 raw INSERT/control/equity ACL prerequisite failed'" in source


def test_revoke_table_and_column_permissions_plus_audit_and_intent_insert():
    source = sql()
    required = (
        "REVOKE UPDATE ON TABLE public.paper_intents FROM hds_paper_executor;",
        "REVOKE UPDATE (status, broker_order_id, updated_at)",
        "REVOKE INSERT ON TABLE public.paper_audit_events FROM hds_paper_executor;",
        "REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor;",
    )
    for statement in required:
        assert statement in source
    assert "has_column_privilege('hds_paper_executor','public.paper_intents','status','UPDATE')" in source
    assert "has_column_privilege('hds_paper_executor','public.paper_intents','broker_order_id','UPDATE')" in source
    assert "has_column_privilege('hds_paper_executor','public.paper_intents','updated_at','UPDATE')" in source
    assert "has_table_privilege('hds_paper_executor','public.paper_audit_events','INSERT')" in source
    assert ("has_table_privilege('hds_paper_executor',"
            in source and "public.paper_equity_snapshots" in source)
    assert "has_column_privilege('hds_paper_reconciler','public.paper_intents','status','UPDATE')" in source
    assert "has_table_privilege('hds_paper_reconciler','public.paper_audit_events','INSERT')" in source


def test_runtime_requires_strict_post_cutover_permissions_not_both_modes():
    from bot.paper_execution_journal import PaperExecutorJournal
    from bot.paper_store import PostgresPaperJournal
    import inspect

    attestation = inspect.getsource(PaperExecutorJournal.assert_executor_permissions)
    reservation = inspect.getsource(PaperExecutorJournal.reserve_if_enabled)
    legacy_observed = inspect.getsource(PostgresPaperJournal.record_observed_state)
    legacy_reserve = inspect.getsource(PostgresPaperJournal.reserve_intent)
    assert "paper_reserve_intent_if_enabled" in attestation
    assert "paper_mark_reserved_intent_unknown" in attestation
    assert "paper_record_reconciled_intent_status" in attestation
    assert "paper_reserve_intent_if_enabled" in reservation
    assert "INSERT INTO public.paper_intents" not in reservation
    assert "raise TradeBlocked" in legacy_observed
    assert "raise TradeBlocked" in legacy_reserve


def test_broker_gateway_never_promotes_its_own_ack():
    from bot import paper_supervised_execution as entry
    import inspect

    gateway = inspect.getsource(entry.submit_paper_once_supervised)
    from bot.paper_execution_journal import PaperExecutorJournal
    authorization = inspect.getsource(PaperExecutorJournal.authorize_reserved_submission)
    assert "journal.authorize_reserved_submission(" in gateway
    assert "request_sha256=request_digest" in gateway
    assert "account_id_sha256=expected_account_sha256" in gateway
    assert gateway.index("assert_lock_alive()") < gateway.index("trading.submit_order(")
    assert "paper_commit_bounded_pilot_dispatch" in authorization
    assert "journal.record_observed_state(" not in gateway
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = False" in inspect.getsource(entry)
