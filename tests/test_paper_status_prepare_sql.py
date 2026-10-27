"""Offline safety gates for the uninstalled PAPER status-function migration.

These tests are lexical contract checks, NOT PostgreSQL execution tests.
Live role/locking tests still need an owner-approved isolated Neon rehearsal.
"""
from pathlib import Path
import re


SQL = (Path(__file__).resolve().parents[1] /
       "sql" / "paper_status_prepare_review_only.sql")


def source():
    return SQL.read_text(encoding="utf-8")


def test_prepare_is_review_only_and_single_atomic_install():
    sql = source()
    assert sql.startswith("-- REVIEW ONLY, NOT INSTALLED")
    assert len(re.findall(r"(?im)^BEGIN;\s*$", sql)) == 1
    assert len(re.findall(r"(?im)^COMMIT;\s*$", sql)) == 1
    assert sql.index("BEGIN;") < sql.index("CREATE ROLE") < sql.index("COMMIT;")
    assert sql.count("SECURITY DEFINER") == 2
    assert sql.count("SET search_path = pg_catalog") == 2
    assert sql.count("FOR UPDATE;") == 2
    assert "GRANT hds_paper_reconciler TO hds_paper_executor" not in sql


def test_executor_can_mark_only_uncertain_and_not_fake_fills():
    sql = source()
    unknown = sql.split("AS $mark_unknown$", 1)[1].split("$mark_unknown$;", 1)[0]
    assert "v_current IS DISTINCT FROM 'reserved'" in unknown
    assert "SET status = 'unknown'" in unknown
    assert "'submission_outcome_unknown'" in unknown
    assert "INSERT INTO public.paper_audit_events" in unknown
    assert "'filled'" not in unknown
    assert re.search(
        r"GRANT EXECUTE ON FUNCTION public\.paper_mark_reserved_intent_unknown"
        r"\(text\)\s+TO hds_paper_executor;",
        sql,
    )


def test_reconciler_is_separate_and_transition_is_transactional():
    sql = source()
    assert "CREATE ROLE hds_paper_reconciler NOLOGIN NOINHERIT;" in sql
    assert "CREATE ROLE hds_paper_status_owner NOLOGIN NOINHERIT;" in sql
    body = sql.split("AS $reconcile$", 1)[1].split("$reconcile$;", 1)[0]
    assert "v_current IS DISTINCT FROM p_expected_status" in body
    assert "WHEN 'reserved' THEN p_new_status IN" in body
    assert "WHEN 'unknown' THEN p_new_status IN" in body
    assert "ELSE FALSE" in body
    assert "v_existing_broker_id IS DISTINCT FROM p_broker_order_id" in body
    assert "INSERT INTO public.paper_audit_events" in body
    assert "p_new_status IN ('submitted','accepted','filled')" in body
    assert re.search(
        r"GRANT EXECUTE ON FUNCTION public\.paper_record_reconciled_intent_status"
        r"\(text,text,text,text\)\s+TO hds_paper_reconciler;",
        sql,
    )
    assert not re.search(
        r"GRANT EXECUTE ON FUNCTION public\.paper_record_reconciled_intent_status"
        r"\(text,text,text,text\)\s+TO hds_paper_executor",
        sql,
    )


def test_prepare_does_not_prematurely_revoke_legacy_writer_access():
    sql = source()
    assert "REVOKE UPDATE" not in sql
    assert "REVOKE INSERT ON TABLE public.paper_audit_events" not in sql
    assert "REVOKE CREATE ON SCHEMA public FROM hds_paper_status_owner;" in sql
    assert "does NOT" in sql or "does not" in sql
